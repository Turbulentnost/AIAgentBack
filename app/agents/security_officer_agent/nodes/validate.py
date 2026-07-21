"""Узлы валидации входной задачи.

Узел ``validate_input`` — проверка задачи по schemas/input.schema.json (тип «обработка»).
Задача, не прошедшая валидацию, отклоняется с фиксацией в audit_logs.
"""

from __future__ import annotations

from typing import Any

from app.platform_sdk import audit_event

from .. import AGENT_ID

REQUIRED_INPUT = ("correlation_id", "tenant_id", "task_type", "counterparty")
TASK_TYPES = (
    "new_supplier_check", "periodic_recheck",
    "contract_check", "exception_check",
)


def validate_input(state: dict[str, Any]) -> dict[str, Any]:
    """Проверяет наличие обязательных полей, корректность task_type и карточки контрагента."""

    errors: list[dict[str, Any]] = []
    for field in REQUIRED_INPUT:
        if not state.get(field):
            errors.append({"type": "missing_field", "field": field})

    # counterparty обязателен и должен содержать наименование (required [name] в схеме)
    counterparty = state.get("counterparty") or {}
    if counterparty and not counterparty.get("name"):
        errors.append({"type": "missing_field", "field": "counterparty.name"})

    task_type = state.get("task_type")
    if task_type and task_type not in TASK_TYPES:
        errors.append({"type": "invalid_task_type", "value": task_type})

    audit_event(
        state.get("correlation_id", ""),
        AGENT_ID,
        "validate_input",
        regulation="ТЗ-ПЛАТФ-001 п. 4.1/10; СТО-28-020 (07)",
        detail={"ok": not errors},
    )
    return {"errors": errors}
