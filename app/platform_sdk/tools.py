"""Шина инструментов агентов (сущности ``agent_tools`` / ``tool_calls``).

Прямое обращение агента к 1С:ERP и внешним ресурсам запрещено (п. 4.5 ТЗ-ПЛАТФ-001).
Узлы вызывают инструменты ТОЛЬКО через ``call_tool``; каждый вызов фиксируется в журнале
``tool_calls`` с correlation_id, параметрами и результатом (проверка T6).

Архитектура (не подключено): фактическое исполнение делегируется заглушке интеграционного
слоя (``integration.py``). Реальные обработчики ERP-инструментов в проде размещаются в
``backend/app/tools/erp/`` и ``backend/app/integrations/onec/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .integration import dispatch

# Реестр tool_calls текущего процесса (in-memory заглушка сущности tool_calls).
# TODO(integration): писать в БД платформы.
_TOOL_CALLS: list["ToolCall"] = []


@dataclass
class ToolCall:
    """Запись вызова инструмента для журнала ``tool_calls``."""

    agent_id: str
    tool_name: str
    correlation_id: str
    params: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    ok: bool = True
    error: str = ""


def call_tool(
    agent_id: str,
    tool_name: str,
    correlation_id: str,
    *,
    write: bool = False,
    hitl_passed: bool = False,
    **params: Any,
) -> ToolCall:
    """Вызывает объявленный инструмент через интеграционный слой и журналирует вызов.

    ``write`` — инструмент записи в 1С (проект документа/вердикт). Запись допускается
    только после прохождения точки HITL: при ``write and not hitl_passed`` вызов
    блокируется (write_gate, п. 4.6; проверка T5). Проведение документов агентом не
    выполняется ни при каких условиях.
    """

    record = ToolCall(agent_id, tool_name, correlation_id, params=dict(params))

    if write and not hitl_passed:
        record.ok = False
        record.error = "write_gate: запись запрещена без прохождения точки HITL"
        _TOOL_CALLS.append(record)
        return record

    try:
        # TODO(integration): реальный вызов integration-service → шлюз 1С / внешний источник.
        record.result = dispatch(tool_name, params, write=write)
        record.ok = True
    except Exception as exc:  # отказоустойчивость: ошибка не роняет граф (T8)
        record.ok = False
        record.error = str(exc)

    _TOOL_CALLS.append(record)
    return record


def get_tool_calls() -> list[ToolCall]:
    """Возвращает журнал вызовов инструментов (для тестов/проверок)."""

    return list(_TOOL_CALLS)


def reset_tool_calls() -> None:
    """Очищает журнал вызовов (между тест-сценариями)."""

    _TOOL_CALLS.clear()
