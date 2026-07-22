"""Узлы сопоставления с критериями, перечня пробелов, оценки риска и уверенности."""

from __future__ import annotations

from typing import Any

from app.platform_sdk import assess_confidence, llm_complete, source_ref

from .. import AGENT_ID


def map_to_criteria(state: dict[str, Any]) -> dict[str, Any]:
    """Ф3. Сопоставление собранных сведений с обязательными критериями конфигурации (правила).

    Перечень обязательных критериев загружается из конфигурации арендатора (не жёсткий код).
    Отмечается покрытие каждого критерия; фиксируется source_reference по каждому критерию.
    При покрытии менее 100% заключение не выдаётся — маршрут на list_gaps.
    """

    # мок: перечень критериев из config/tenants/<tenant>.yaml (здесь — заглушка)
    criteria = [
        {"id": "registration", "covered": bool(state.get("external_data"))},
        {"id": "settlements", "covered": bool(state.get("registry_data"))},
        {"id": "history", "covered": bool(state.get("supply_history"))},
        {"id": "affiliation", "covered": bool(state.get("affiliation"))},
    ]
    covered = sum(1 for c in criteria if c["covered"])
    coverage = round(100.0 * covered / len(criteria), 1) if criteria else 0.0
    refs = [source_ref("СТО-28-020", clause="критерии проверки контрагента", version="07",
                       accessed_at=state.get("_today", ""), status="проверенный")]
    return {"criteria": criteria, "coverage": coverage, "source_references": refs}


def list_gaps(state: dict[str, Any]) -> dict[str, Any]:
    """Ф3. Перечень недостающих сведений при покрытии критериев < 100% (правила).

    Заключение не выдаётся; формируется перечень пробелов, data_confidence = low.
    """

    gaps = [c["id"] for c in (state.get("criteria") or []) if not c.get("covered")]
    findings = [{
        "type": "criteria_coverage_gap",
        "severity": "major",
        "description": f"Покрытие обязательных критериев < 100%; недостающие: {', '.join(gaps) or '—'}",
        "source": "map_to_criteria + конфигурация арендатора",
        "recommendation": "Дособрать сведения по недостающим критериям; заключение не выдаётся",
    }]
    return {"gaps": gaps, "findings": findings, "requires_human_review": True,
            "errors": [{"type": "criteria_coverage_gap"}]}


def risk_scoring(state: dict[str, Any]) -> dict[str, Any]:
    """Ф4. Расчёт уровня риска и перечня факторов (правила + LLM).

    risk_level ∈ {low, medium, high} по правилам конфигурации; учитываются предусмотренные
    исключения арендатора. Контроль ложных срабатываний: доля необоснованных блокировок
    безопасных контрагентов — не более 5% (guardrail-KPI K3).
    """

    llm_complete("Оценить уровень риска контрагента и перечислить факторы",
                 system="risk_scoring", mask_pii=True)

    external = state.get("external_data") or {}
    history = state.get("supply_history") or {}
    risk_factors: list[dict[str, Any]] = []

    # мок-правило: молодая организация без истории исполненных договоров → средний риск
    if external.get("registered_months", 99) < 12 or history.get("contracts_executed", 0) == 0:
        risk_factors.append({
            "type": "risk_factor",
            "severity": "major",
            "description": "Срок регистрации менее 12 месяцев либо отсутствует история "
                           "исполненных договоров",
            "source": "Официальный реестр (статус: официальный); карточка контрагента 1С",
            "recommendation": "Допуск при условии 100% постоплаты и лимита суммы договора",
        })

    if risk_factors:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {"risk_level": risk_level, "risk_factors": risk_factors, "findings": risk_factors}


def assess_confidence_node(state: dict[str, Any]) -> dict[str, Any]:
    """Узел assess_confidence: расчёт data_confidence и requires_human_review (правила).

    Условия снижения уверенности (раздел 8.2 ТЗ) — см. confidence.yaml:
      * контрагент не идентифицируется однозначно (нет ИНН / несколько карточек / расхождение);
      * покрытие обязательных критериев < 100% (заключение не выдаётся);
      * внешний источник недоступен либо «неофициальный» / «требует проверки»;
      * дата обращения превышает срок актуальности из конфигурации;
      * данные 1С и официального реестра расходятся;
      * выявлен критический фактор — не выше medium + утверждение руководителя СБ;
      * признаки связанности выявлены, но не подтверждены документально;
      * исключение применено, но не подтверждено конфигурацией арендатора.
    """

    return assess_confidence(state)
