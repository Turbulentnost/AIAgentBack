from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from app.agents.builder.preview_tool_params import (
    has_substantive_preview_data,
    infer_preview_tool_params,
)
from app.agents.tools.registry import agent_tool_registry
from app.agents.tools.schemas import EmptyToolInput, ToolContext
from app.agents.tools.system_tools import resolve_current_date
from app.core.logging import get_logger

logger = get_logger(__name__)


def _candidate_tool_names(blueprint: dict[str, Any] | None, requirements: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for source in (
        (blueprint or {}).get("tools"),
        requirements.get("recommended_tools"),
    ):
        if isinstance(source, list):
            for item in source:
                if isinstance(item, str) and item.strip():
                    names.append(item.strip())
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _dump_tool_result(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return jsonable_encoder(value)


async def collect_preview_grounding(
    *,
    blueprint: dict[str, Any] | None,
    requirements: dict[str, Any],
    goal: str = "",
    db: Any | None = None,
    user: Any | None = None,
) -> dict[str, Any]:
    """Пробный запуск: реальное выполнение tools из blueprint + проверенная дата."""
    current_date = resolve_current_date().model_dump()
    tool_results: dict[str, Any] = {"get_current_date": current_date}
    skipped_tools: list[str] = []
    errors: dict[str, str] = {}

    blueprint_tools = _candidate_tool_names(blueprint, requirements)
    to_run: list[str] = []
    for name in blueprint_tools:
        if name not in to_run:
            to_run.append(name)
    for definition in agent_tool_registry.list():
        if definition.preview_always and definition.name not in to_run:
            to_run.append(definition.name)

    for tool_name in to_run:
        if tool_name == "get_current_date":
            continue
        definition = agent_tool_registry.get(tool_name)
        if definition is None or not definition.implemented or definition.handler is None:
            skipped_tools.append(tool_name)
            continue

        params = infer_preview_tool_params(tool_name, goal, requirements)
        if params is None and not definition.preview_always:
            skipped_tools.append(tool_name)
            continue

        if db is None or user is None:
            skipped_tools.append(tool_name)
            continue

        try:
            payload = (
                definition.input_model(**params)
                if definition.input_model is not None
                else EmptyToolInput()
            )
            context = ToolContext(db=db, user=user)
            response = await definition.handler(payload, context)
            tool_results[tool_name] = _dump_tool_result(response)
        except Exception as exc:
            logger.warning("builder.preview_grounding_tool_failed", tool=tool_name, error=str(exc))
            errors[tool_name] = str(exc)

    substantive = has_substantive_preview_data(tool_results)
    return {
        "current_date": current_date,
        "tool_results": tool_results,
        "skipped_tools": skipped_tools,
        "errors": errors,
        "has_substantive_data": substantive,
        "mode": "tool_execution" if substantive else "partial_grounding",
    }
