"""Журнал аудита и прослеживаемости (сущность ``audit_logs``).

Требование ТЗ: каждый переход графа и каждое действие агента фиксируется с correlation_id
и нормативным основанием (проверка T9, KPI прослеживаемости). Здесь — in-memory заглушка;
в проде пишется в БД платформы.
"""

from __future__ import annotations

from typing import Any

# TODO(integration): заменить in-memory список на запись в сущность audit_logs (PostgreSQL).
_AUDIT_LOG: list[dict[str, Any]] = []


def audit_event(
    correlation_id: str,
    agent_id: str,
    action: str,
    *,
    regulation: str = "",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Регистрирует событие аудита. Возвращает записанную строку журнала."""

    entry = {
        "correlation_id": correlation_id,
        "agent_id": agent_id,
        "action": action,
        "regulation": regulation,   # нормативное основание (СТО/ПЛ/пункт)
        "detail": detail or {},
    }
    _AUDIT_LOG.append(entry)
    return entry


def get_audit_log() -> list[dict[str, Any]]:
    """Возвращает журнал аудита (для тестов и проверок ОПЭ)."""

    return list(_AUDIT_LOG)


def reset_audit_log() -> None:
    """Очищает журнал (используется в тестах между сценариями)."""

    _AUDIT_LOG.clear()
