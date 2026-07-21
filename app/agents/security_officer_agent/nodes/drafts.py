"""Узлы формирования проектов заключения, точки HITL и фиксации вердикта.

Все действия — на уровне У1: агент формирует ТОЛЬКО проект заключения. Утверждение
выполняется сотрудником СБ (при критических факторах — руководителем СБ) через точку
human-in-the-loop (hitl.yaml). Фиксация вердикта в 1С:ERP выполняется агентом ТОЛЬКО
после узла human_approve — через call_tool(..., write=True, hitl_passed=True).
"""

from __future__ import annotations

from typing import Any

from app.platform_sdk import call_tool, human_gate, llm_complete

from .. import AGENT_ID


def _cid(state: dict[str, Any]) -> str:
    return state.get("correlation_id", "")


def draft_conclusion_reject(state: dict[str, Any]) -> dict[str, Any]:
    """Ф5. Проект заключения «ОТКАЗ» с перечнем критических факторов (LLM + шаблон).

    Высокий риск. Фиксация вердикта — только после human_approve (руководитель СБ).
    """

    llm_complete("Сформировать проект заключения ОТКАЗ с перечнем критических факторов",
                 system="draft_conclusion", mask_pii=True)
    call_tool(AGENT_ID, "docs.render_template", _cid(state), template="counterparty_verdict")
    return {"verdict": "reject", "requires_human_review": True}


def draft_conclusion_conditional(state: dict[str, Any]) -> dict[str, Any]:
    """Ф5. Проект заключения «УСЛОВИЯ ДОПУСКА» (LLM + шаблон).

    Средний риск: предоплата, обеспечение, лимит суммы, сокращённый срок договора,
    дополнительный контроль. Фиксация вердикта — только после human_approve.
    """

    llm_complete("Сформировать проект заключения УСЛОВИЯ ДОПУСКА",
                 system="draft_conclusion", mask_pii=True)
    call_tool(AGENT_ID, "docs.render_template", _cid(state), template="counterparty_verdict")
    conditions = ["100% постоплата", "лимит суммы договора", "дополнительный контроль поставок"]
    return {"verdict": "conditional", "conditions": conditions, "requires_human_review": True}


def draft_conclusion_approve(state: dict[str, Any]) -> dict[str, Any]:
    """Ф5. Проект заключения «СОГЛАСОВАНИЕ» (LLM + шаблон).

    Низкий риск. Фиксация вердикта — только после human_approve.
    """

    llm_complete("Сформировать проект заключения СОГЛАСОВАНИЕ",
                 system="draft_conclusion", mask_pii=True)
    call_tool(AGENT_ID, "docs.render_template", _cid(state), template="counterparty_verdict")
    return {"verdict": "approve", "requires_human_review": True}


def human_approve(state: dict[str, Any]) -> dict[str, Any]:
    """HITL: утверждение заключения по контрагенту (interrupt).

    Согласование / отказ / условия допуска утверждает сотрудник СБ; при критических
    факторах риска — руководитель СБ. Агент не исполняет действие сам.
    """

    critical = any(f.get("severity") == "critical" for f in state.get("risk_factors", []) or [])
    approver = ("Руководитель службы безопасности" if critical
                else "Сотрудник службы безопасности")
    human_gate("Утверждение заключения по контрагенту (согласование / отказ / условия допуска)",
               approver, correlation_id=_cid(state))
    return {"requires_human_review": True}


def write_counterparty_verdict(state: dict[str, Any]) -> dict[str, Any]:
    """Ф5. Фиксация заключения, уровня риска и условий допуска в 1С:ERP (инструмент).

    Выполняется ТОЛЬКО после узла human_approve — запись через белый список HTTP-сервиса
    (write=True, hitl_passed=True). Проведение документов агентом не выполняется.
    """

    call_tool(AGENT_ID, "erp.write_counterparty_verdict", _cid(state),
              write=True, hitl_passed=True,
              counterparty_id=state.get("counterparty_id"),
              verdict=state.get("verdict"),
              risk_level=state.get("risk_level"),
              conditions=state.get("conditions", []))
    return {}
