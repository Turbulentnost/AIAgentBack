"""Интеграционный слой инструментов агентов (1С MCP + внешние источники).

Боевой режим: если привязан :class:`AgentRuntime` с MCP-клиентом 1С, инструмент
чтения с известной боевой capability вызывается через ``OneCMCPClient.call_capability``
(мост sync→async). Инструменты без боевой capability и внешние источники (рынок
поставщиков, официальные реестры, парсинг документов) возвращают честный
``capability_unavailable`` — БЕЗ выдуманных данных. Запись в 1С недоступна: MCP
работает в режиме ``READ_ONLY`` (``write_unavailable``).

Резервный режим (runtime не привязан): прежние детерминированные мок-данные, чтобы
графы проходили end-to-end без боевых сервисов.
"""

from __future__ import annotations

from typing import Any

WHITELIST_WRITE_METHODS = ("/hs/agents/v1/",)

# Карта «имя инструмента агента → боевая 1С-capability MCP».
# Заполняется по мере публикации capability на MCP-сервере 1С. Инструменты, которых
# здесь нет, честно возвращают capability_unavailable (не мок-успех).
# На текущий момент боевые capability MCP заточены под производственный поток и не
# покрывают справочники ОМТО (история поставщика, договоры, контрагенты, взаиморасчёты),
# поэтому карта пуста. Точка расширения — добавить сюда пары {tool: capability}.
TOOL_CAPABILITY_MAP: dict[str, str] = {}

_EXTERNAL_TOOL_PREFIXES = ("market.", "ext.", "docs.", "notify.")


def dispatch(tool_name: str, params: dict[str, Any], *, write: bool = False) -> Any:
    """Диспетчеризует вызов инструмента: боевой MCP 1С либо честная недоступность."""

    try:
        from app.agents.omto_role_agents.runtime_context import current_runtime, run_async
    except Exception:  # noqa: BLE001
        return _mock(tool_name, params, write=write)

    runtime = current_runtime()
    if runtime is None:
        # Dry-run / тесты без боевого контекста — прежние мок-данные.
        return _mock(tool_name, params, write=write)

    if write:
        return {
            "status": "write_unavailable",
            "tool": tool_name,
            "reason": "Запись в 1С недоступна: MCP работает в режиме READ_ONLY.",
        }

    if tool_name.startswith(_EXTERNAL_TOOL_PREFIXES):
        return {
            "status": "capability_unavailable",
            "tool": tool_name,
            "reason": "Внешний источник/сервис не подключён к платформе.",
        }

    capability = TOOL_CAPABILITY_MAP.get(tool_name)
    if capability is None or runtime.mcp is None:
        return {
            "status": "capability_unavailable",
            "tool": tool_name,
            "reason": "Нет боевой 1С-capability для инструмента на MCP-сервере.",
        }

    try:
        return run_async(runtime.mcp.call_capability(capability, params))
    except Exception as exc:  # noqa: BLE001 — сбой источника не роняет граф
        return {"status": "capability_unavailable", "tool": tool_name, "reason": str(exc)}


def _mock(tool_name: str, params: dict[str, Any], *, write: bool = False) -> dict[str, Any]:
    if write or tool_name in _MOCK_WRITE_TOOLS:
        return {"_mock": True, "draft_id": "DRAFT-MOCK-0001", "posted": False}
    return {"_mock": True, "tool": tool_name, "data": {}, "record_version": "mock-0"}


_MOCK_WRITE_TOOLS = {
    "erp.draft_purchase_order",
    "erp.link_invoice",
    "erp.write_deviation_approval",
    "erp.set_responsible_manager",
    "erp.write_decision",
    "erp.write_counterparty_verdict",
}
