"""Узлы валидации входной задачи.

Узел ``validate_input`` — проверка задачи по schemas/input.schema.json (тип «обработка»).
Задача, не прошедшая валидацию, отклоняется с фиксацией в audit_logs.
"""

from __future__ import annotations

from typing import Any

from app.platform_sdk import audit_event

from .. import AGENT_ID

REQUIRED_INPUT = ("correlation_id", "tenant_id", "task_type")
TASK_TYPES = (
    "periodic_control", "case_review", "price_deviation_review", "daily_report",
)


def validate_input(state: dict[str, Any]) -> dict[str, Any]:
    """Проверяет наличие обязательных полей и корректность task_type."""

    errors: list[dict[str, Any]] = []
    for field in REQUIRED_INPUT:
        if not state.get(field):
            errors.append({"type": "missing_field", "field": field})

    task_type = state.get("task_type")
    if task_type and task_type not in TASK_TYPES:
        errors.append({"type": "invalid_task_type", "value": task_type})

    audit_event(
        state.get("correlation_id", ""),
        AGENT_ID,
        "validate_input",
        regulation="ТЗ-ПЛАТФ-001 п. 4.1/10",
        detail={"ok": not errors},
    )
    return {"errors": errors}
