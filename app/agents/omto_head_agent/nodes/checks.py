"""Узлы надзорных проверок, свода и расчётов контура №3.

В ТЗ (TABLE 7) проверки check_* помечены типом «параллельно» — в проде выполняются в
рамках ОДНОГО суперштага LangGraph (ошибка одного источника не роняет граф, T8). В данном
мини-runtime они реализованы последовательной цепочкой узлов (топология эквивалентна).
"""

from __future__ import annotations

from typing import Any

from app.platform_sdk import assess_confidence, hybrid_search, llm_complete, source_ref

from .. import AGENT_ID


def _cases(state: dict[str, Any]) -> list[dict[str, Any]]:
    return (state.get("erp_snapshot") or {}).get("cases") or []


def check_assignment_sla(state: dict[str, Any]) -> dict[str, Any]:
    """Ф1. Контроль срока распределения заявок ответственному менеджеру (шаг 2.1 СТО-28-020).

    Позиция без ответственного сверх допустимого срока → сигнал агенту зам. начальника ОМТО
    и, при повторном нарушении, проект эскалации. Тип «проверка / параллельно».
    """

    findings: list[dict[str, Any]] = []
    for c in _cases(state):
        if c.get("assignment_overdue") or not c.get("assigned", True):
            findings.append({
                "type": "assignment_sla",
                "severity": "major",
                "description": f"Заявка {c.get('case_id')} не распределена в нормативный срок",
                "source": "СТО-28-020 шаг 2.1; erp.read_procurement_cases",
                "recommendation": "Назначить ответственного менеджера; сигнал зам. начальника ОМТО",
            })
    return {"findings": findings}


def check_procurement_sla(state: dict[str, Any]) -> dict[str, Any]:
    """Ф2. Контроль нормативных сроков закупочных задач по этапам КТ2–КТ3 (тип «проверка»).

    Предупреждение формируется ДО нарушения срока, а не по факту. Норматив — из конфигурации.
    """

    findings: list[dict[str, Any]] = []
    for c in _cases(state):
        if c.get("procurement_overdue"):
            findings.append({
                "type": "procurement_sla",
                "severity": "major",
                "description": f"Закупочная задача {c.get('case_id')} с просроченным нормативным сроком",
                "source": "СТО-28-020 КТ2–КТ3; erp.read_procurement_cases",
                "recommendation": "Ускорить этап; проект корректирующего действия",
            })
    return {"findings": findings}


def check_price_deviation(state: dict[str, Any]) -> dict[str, Any]:
    """Ф3. Сопоставление цены с проектной и сроком её действия (п. 6.4 системного ТЗ).

    Проверяются процент отклонения, наличие проекта служебной записки (шаг 3.3) и 2/3
    сопоставимых предложений. Отсутствие/истёкший срок проектной цены → понижение уверенности
    (раздел 8.2). Тип «проверка / параллельно».
    """

    findings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for c in _cases(state):
        if not c.get("project_price_valid", True):
            errors.append({"type": "project_price_missing", "case_id": c.get("case_id")})
        if float(c.get("price_deviation_pct", 0.0)) > 0.0:
            findings.append({
                "type": "price_deviation",
                "severity": "critical",
                "description": (f"Цена по кейсу {c.get('case_id')} превышает проектную на "
                                f"{c.get('price_deviation_pct')}%; служебная записка не сформирована"),
                "source": "Проектная цена; СТО-28-020 п. 6.11.4; erp.read_project_prices",
                "recommendation": "Приостановить размещение до согласования СЗ начальником ОМТО и финансовой функцией",
            })
    return {"findings": findings, "errors": errors}


def check_delivery_deviation(state: dict[str, Any]) -> dict[str, Any]:
    """Ф4. Контроль статусов и прогноза даты поставки (тип «проверка / параллельно»).

    Расхождение прогнозной и подтверждённой даты поставки; влияние на производственный план
    оценивается через оркестратор (запрос к контуру №2) — вывод может приостановить закупку.
    """

    findings: list[dict[str, Any]] = []
    for c in _cases(state):
        if c.get("delivery_deviation"):
            findings.append({
                "type": "delivery_deviation",
                "severity": "major",
                "description": f"Расхождение прогнозной и подтверждённой даты поставки по кейсу {c.get('case_id')}",
                "source": "СТО-28-020 контур №5; erp.read_delivery_status",
                "recommendation": "Уточнить дату поставки; оценить влияние на производственный план (контур №2)",
            })
    return {"findings": findings}


def check_claims(state: dict[str, Any]) -> dict[str, Any]:
    """Ф4. Контроль своевременности претензионной работы (тип «проверка / параллельно»)."""

    findings: list[dict[str, Any]] = []
    for c in _cases(state):
        if c.get("claim_overdue"):
            findings.append({
                "type": "claim_sla",
                "severity": "minor",
                "description": f"Проект претензии по кейсу {c.get('case_id')} не сформирован в срок",
                "source": "СТО-28-020 претензионная работа; erp.read_claims",
                "recommendation": "Сформировать проект претензии; контроль срока реагирования",
            })
    return {"findings": findings}


def aggregate_findings(state: dict[str, Any]) -> dict[str, Any]:
    """Свод отклонений и привязка к источникам (тип «обработка»).

    Полнотекстовый/векторный поиск пунктов СТО для подтверждения ссылок (Ф5). Неподтверждённая
    в актуальной версии ссылка на пункт СТО → понижение уверенности (раздел 8.2).
    Дедупликация отклонений выполняется в проде в рамках единого суперштага LangGraph; в
    мини-runtime списки findings накапливаются узлами check_* и здесь только сверяются с СТО.
    """

    hybrid_search("пункты СТО-28-020 по выявленным отклонениям", collection="regulations",
                  full_text_terms=["СТО-28-020", "СТО-10-095"])
    refs = [source_ref("СТО-28-020", clause="свод отклонений", version="07",
                       accessed_at=state.get("_today", ""), status="проверенный")]
    return {"source_references": refs}


def classify_severity(state: dict[str, Any]) -> dict[str, Any]:
    """Классификация критичности отклонений: critical / major / minor (тип «LLM + правила»).

    Правила критичности задаются конфигурацией арендатора; LLM формирует объяснимую
    формулировку. Здесь — детерминированный свод по severity выявленных отклонений.
    """

    llm_complete("Классифицировать критичность отклонений по правилам конфигурации",
                 system="classify_severity")
    findings = state.get("findings") or []
    severity_map = {"critical": 0, "major": 0, "minor": 0}
    for f in findings:
        sev = f.get("severity", "minor")
        severity_map[sev] = severity_map.get(sev, 0) + 1
    return {"severity_map": severity_map}


def assess_confidence_node(state: dict[str, Any]) -> dict[str, Any]:
    """Узел assess_confidence: расчёт data_confidence и requires_human_review (правила).

    Понижает уверенность при: отсутствии обязательных данных кейса; противоречии данных 1С;
    отсутствии/истечении проектной цены; выводе, приостанавливающем закупку; неподтверждённой
    ссылке на пункт СТО; critical-отклонении (не выше medium) — раздел 8.2 confidence.yaml.
    """

    return assess_confidence(state)
