"""Узел формирования структурированного результата (schemas/output.schema.json)."""

from __future__ import annotations

from typing import Any

from app.platform_sdk import audit_event

from .. import AGENT_ID


def emit_result(state: dict[str, Any]) -> dict[str, Any]:
    """Формирует результат по образцу п. 4.8: agent_id, status, summary, findings[],
    data_confidence, requires_human_review. Тип «обработка»."""

    errors = state.get("errors") or []
    findings = state.get("findings") or []

    if any(e.get("type") == "missing_field" for e in errors):
        status = "needs_input"
    elif findings:
        status = "completed_with_issues"
    else:
        status = "completed"

    summary = _summary(state, status)

    result = {
        "agent_id": AGENT_ID,
        "status": status,
        "summary": summary[:300],
        "findings": findings,
        "data_confidence": state.get("data_confidence", "medium"),
        "requires_human_review": bool(state.get("requires_human_review", False)),
    }
    audit_event(state.get("correlation_id", ""), AGENT_ID, "emit_result",
                regulation="ТЗ-ПЛАТФ-001 п. 4.8", detail={"status": status})
    return {"result": result}


def _summary(state: dict[str, Any], status: str) -> str:
    task = state.get("task_type", "")
    n = len(state.get("findings") or [])
    return f"Задача {task}: статус {status}; наблюдений — {n}."
