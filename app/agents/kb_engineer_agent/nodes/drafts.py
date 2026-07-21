"""Узлы формирования проектов технического заключения, точки HITL и записи в 1С.

Все действия — на уровне У1: агент формирует ТОЛЬКО проект технического заключения.
Утверждение выполняется человеком (узел ``human_approve``); при критическом влиянии —
руководителем КБ/ГСПП. Фиксация согласованного отклонения в 1С — через
``call_tool(..., write=True, hitl_passed=...)`` и только после прохождения узла HITL.
"""

from __future__ import annotations

from typing import Any

from app.platform_sdk import call_tool, human_gate, llm_complete

from .. import AGENT_ID


def _cid(state: dict[str, Any]) -> str:
    return state.get("correlation_id", "")


def draft_conclusion_deny(state: dict[str, Any]) -> dict[str, Any]:
    """Ф5. Проект технического заключения «ЗАПРЕТ» + обоснование по критериям (LLM).

    Формируется при покрытии обязательных критериев менее 100% либо при безусловном
    запрете применения по оценке риска. Согласованное отклонение НЕ фиксируется —
    ветвь идёт на расчёт уверенности и результат (без записи в 1С).
    """

    llm_complete("Сформировать проект заключения «ЗАПРЕТ» с обоснованием по каждому критерию",
                 system="draft_conclusion_deny")
    call_tool(AGENT_ID, "docs.render_template", _cid(state), template="conclusion_deny")

    comparison = state.get("comparison") or []
    unmet = [c["requirement"] for c in comparison if not c.get("present")]
    finding = {
        "type": "conclusion_deny",
        "severity": "critical",
        "description": "Проект заключения: ЗАПРЕТ применения аналога/замены; "
                       f"не покрыты обязательные критерии: {', '.join(unmet) or '—'}",
        "source": "КД (действующая версия); СТО-28-020 (версия 07), шаг 2.5",
        "recommendation": "Утверждение инженером КБ/ГСПП; при критическом влиянии — руководителем",
    }
    return {"verdict": "deny", "findings": [finding], "requires_human_review": True}


def draft_conclusion_allow(state: dict[str, Any]) -> dict[str, Any]:
    """Ф5. Проект заключения «РАЗРЕШЕНИЕ» + условия применения + изменения КД (LLM).

    Проект формируется без фиксации отклонения. Пробная запись в 1С до узла HITL
    заблокирована (hitl_passed=False, write_gate) — фиксация выполняется узлом
    write_deviation_approval уже после human_approve.
    """

    llm_complete("Сформировать проект заключения «РАЗРЕШЕНИЕ» с условиями и изменениями КД",
                 system="draft_conclusion_allow")
    call_tool(AGENT_ID, "docs.render_template", _cid(state), template="conclusion_allow")

    # Требуемые изменения КД по выявленным несоответствиям (характеристики диапазона и т.п.).
    kd_changes = [
        "Внести изменение в КД в части диапазона рабочих температур либо ограничить область применения",
    ]
    # Пробная запись ДО HITL — блокируется write_gate (hitl_passed=False):
    call_tool(AGENT_ID, "erp.write_deviation_approval", _cid(state),
              write=True, hitl_passed=False, position_id=state.get("position_id"))
    return {"verdict": "allow", "kd_changes": kd_changes}


def human_approve(state: dict[str, Any]) -> dict[str, Any]:
    """HITL: утверждение технического заключения инженером КБ/ГСПП; при критическом
    влиянии — руководителем КБ/ГСПП (interrupt). Агент не утверждает заключение сам.
    """

    findings = state.get("findings") or []
    critical = any(f.get("severity") == "critical" for f in findings) or \
        (state.get("risk") or {}).get("critical")
    approver = "Руководитель КБ / ГСПП" if critical else "Инженер КБ / ГСПП"
    human_gate("Утверждение технического заключения (разрешение/запрет аналога или замены)",
               approver, correlation_id=_cid(state))
    return {"requires_human_review": True}


def write_deviation_approval(state: dict[str, Any]) -> dict[str, Any]:
    """Ф5. Фиксация согласованного отклонения от КД в 1С ДО заказа (инструмент).

    Выполняется только после узла human_approve: запись разрешена (hitl_passed=True) по
    белому списку HTTP-сервиса 1С. Проведение документов не выполняется; создаётся проект
    записи. Результат передаётся агенту менеджера по закупкам (шаг 2.5).
    """

    call_tool(AGENT_ID, "erp.write_deviation_approval", _cid(state),
              write=True, hitl_passed=True,
              position_id=state.get("position_id"),
              conditions=state.get("conditions", []),
              kd_changes=state.get("kd_changes", []))
    call_tool(AGENT_ID, "notify.route", _cid(state), target="procurement_manager_agent",
              deviation="approved")
    return {}
