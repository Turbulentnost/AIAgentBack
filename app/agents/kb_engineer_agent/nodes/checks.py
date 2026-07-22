"""Узлы сопоставления, оценки допустимости, риска и расчёта уверенности."""

from __future__ import annotations

from typing import Any

from app.platform_sdk import assess_confidence, hybrid_search, llm_complete, source_ref

from .. import AGENT_ID


def _cid(state: dict[str, Any]) -> str:
    return state.get("correlation_id", "")


def compare_characteristics(state: dict[str, Any]) -> dict[str, Any]:
    """Ф4. Построчное сопоставление требование ↔ аналог; расчёт покрытия (LLM + правила).

    Покрытие = доля обязательных характеристик, по которым аналог предоставил данные.
    Несовпадение диапазона/значения фиксируется отдельным finding, но не снижает покрытие.
    При покрытии менее 100% заключение не выдаётся (маршрут на draft_conclusion_deny).
    """

    llm_complete("Сопоставить обязательные требования КД с характеристиками аналога",
                 system="compare_characteristics")

    requirements = state.get("requirements") or []
    analog_chars = (state.get("analog") or {}).get("characteristics", {}) or {}
    mandatory = [r for r in requirements if r.get("mandatory")]

    comparison: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    covered = 0
    for req in mandatory:
        key = req.get("key")
        analog_value = analog_chars.get(key)
        present = analog_value is not None
        matched = present and analog_value == req.get("value")
        if present:
            covered += 1
        comparison.append({
            "requirement": key,
            "required": req.get("value"),
            "analog": analog_value,
            "matched": matched,
            "present": present,
        })
        if present and not matched:
            findings.append({
                "type": "characteristic_mismatch",
                "severity": "major",
                "description": f"Характеристика '{key}': аналог {analog_value} "
                               f"против требуемого {req.get('value')} по КД",
                "source": "КД (действующая версия), таблица характеристик; паспорт аналога, разд. 2",
                "recommendation": "Ограничить область применения либо внести изменение в КД",
            })

    coverage = (covered / len(mandatory) * 100.0) if mandatory else 0.0
    return {"comparison": comparison, "coverage": coverage, "findings": findings}


def check_applicability(state: dict[str, Any]) -> dict[str, Any]:
    """Ф4. Ограничения применения, совместимость, ресурс, условия эксплуатации (LLM + RAG).

    Формирует перечень обязательных условий применения (conditions) для проекта заключения.
    """

    hybrid_search("ограничения применения, совместимость, ресурс, условия эксплуатации",
                  collection="kd", full_text_terms=[str(state.get("position_id", ""))])
    llm_complete("Проверить допустимость применения аналога с учётом ограничений и ресурса",
                 system="check_applicability")

    conditions = [
        "Применение только для позиций внутреннего монтажа",
        "Подтвердить ресурс в заявленных условиях эксплуатации",
    ]
    refs = [source_ref("СТО-28-020", clause="шаг 2.5", version="07",
                       accessed_at=state.get("_today", ""), status="официальный")]
    return {"conditions": conditions, "source_references": refs}


def assess_risk(state: dict[str, Any]) -> dict[str, Any]:
    """Ф4. Оценка влияния замены на изделие, производство и входной контроль (LLM).

    При критическом влиянии — уверенность не выше medium и обязательное подтверждение
    руководителя КБ/ГСПП (реализуется через finding severity=critical и confidence.yaml).
    """

    llm_complete("Оценить влияние замены на изделие, производство и входной контроль",
                 system="assess_risk")

    # Мок: влияние не критическое (управляемо условиями применения).
    risk = {
        "impact_product": "ограниченное",
        "impact_production": "низкое",
        "impact_incoming_control": "низкое",
        "critical": False,
        "prohibitive": False,   # true → безусловный запрет применения
    }
    findings: list[dict[str, Any]] = []
    if risk["critical"]:
        findings.append({
            "type": "critical_impact",
            "severity": "critical",
            "description": "Критическое влияние замены на изделие",
            "source": "Оценка риска КБ/ГСПП; КД (действующая версия)",
            "recommendation": "Требуется утверждение руководителя КБ/ГСПП; уверенность ≤ medium",
        })
    return {"risk": risk, "findings": findings}


def assess_confidence_node(state: dict[str, Any]) -> dict[str, Any]:
    """Узел assess_confidence: расчёт data_confidence и requires_human_review (правила)."""

    return assess_confidence(state)
