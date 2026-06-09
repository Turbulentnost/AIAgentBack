from __future__ import annotations

from typing import Any

from app.agents.builder.capabilities import get_capability_label
from app.core.logging import get_logger

logger = get_logger(__name__)

RUNTIME_TOOL_LABELS = {
    "web_search": "веб-поиск",
    "fetch_page_via_user_browser": "просмотр страниц через браузер",
    "search_knowledge_base": "поиск в базе знаний",
    "list_available_knowledge_bases": "список баз знаний",
    "get_knowledge_fragment": "чтение фрагмента базы знаний",
    "get_current_date": "текущая дата",
}


def _capabilities_from_blueprint(blueprint: dict[str, Any]) -> list[str]:
    nodes = ((blueprint or {}).get("workflow_graph") or {}).get("nodes") or []
    capabilities: list[str] = []
    for node in nodes:
        capability = node.get("capability")
        if not capability or capability == "human_approval":
            continue
        if capability not in capabilities:
            capabilities.append(capability)
    return capabilities


def build_blueprint_summary(
    *,
    goal: str,
    requirements: dict[str, Any],
    blueprint: dict[str, Any] | None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Статическая сводка структуры агента БЕЗ выполнения инструментов.

    Конструктор не делает HTTP/браузерных вызовов и не запускает tools — он только
    описывает blueprint и его runtime-зависимости. Реальное выполнение происходит
    исключительно в Runtime Sandbox.
    """
    blueprint = blueprint or {}
    agent_type = blueprint.get("agent_type") or requirements.get("agent_type") or "consultant"
    card = blueprint.get("agent_card") or {}
    name = card.get("name") or "—"
    tools = [tool for tool in (blueprint.get("tools") or []) if isinstance(tool, str)]
    capabilities = _capabilities_from_blueprint(blueprint)
    input_params = list(((blueprint.get("input_schema") or {}).get("properties") or {}).keys())
    output_format = list(((blueprint.get("output_schema") or {}).get("properties") or {}).keys())

    valid = bool((validation or {}).get("valid", True))
    errors = list((validation or {}).get("errors") or [])

    capability_labels = [get_capability_label(item) for item in capabilities] or ["—"]
    runtime_labels = [RUNTIME_TOOL_LABELS.get(tool, tool) for tool in tools] or ["—"]
    type_label = "Консультант" if agent_type == "consultant" else "Действие"

    lines = [
        f"Тип агента: {type_label}",
        "Blueprint сформирован успешно" if valid else f"Blueprint неполный: {', '.join(errors)}",
        "",
        f"Имя агента: {name}",
        f"Требуемые возможности: {', '.join(capability_labels)}",
        f"Runtime-зависимости (инструменты): {', '.join(runtime_labels)}",
    ]
    if input_params:
        lines.append(f"Входные параметры: {', '.join(input_params)}")
    if output_format:
        lines.append(f"Формат вывода: {', '.join(output_format)}")
    lines += [
        "",
        "Это статическая проверка структуры. Конструктор не выполняет инструменты, "
        "браузерные и сетевые вызовы.",
        "Для реального прогона агента с живыми данными используйте Runtime Sandbox "
        "в панели по центру.",
    ]

    return {
        "success": valid,
        "summary_type": "static_validation",
        "output_text": "\n".join(lines),
        "agent_type": agent_type,
        "capabilities": capabilities,
        "runtime_dependencies": tools,
        "input_params": input_params,
        "output_format": output_format,
        "valid": valid,
        "errors": errors,
    }
