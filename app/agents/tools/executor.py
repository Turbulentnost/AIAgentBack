from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from app.agents.tools.registry import agent_tool_registry
from app.agents.tools.schemas import EmptyToolInput, ToolContext
from app.models.agent import ToolCall


class ToolExecutionError(RuntimeError):
    pass


class ToolExecutor:
    async def invoke(
        self,
        *,
        tool_name: str,
        params: dict[str, Any],
        context: ToolContext,
        allowed_tools: list[str] | None = None,
    ) -> Any:
        if allowed_tools is not None and tool_name not in allowed_tools:
            raise ToolExecutionError(f"Инструмент '{tool_name}' не разрешен текущему агенту")

        definition = agent_tool_registry.get(tool_name)
        if definition is None or definition.handler is None:
            raise ToolExecutionError(f"Инструмент '{tool_name}' не зарегистрирован или не реализован")

        started = perf_counter()
        call = ToolCall(
            agent_id=context.agent_id,
            task_id=context.task_id,
            tool_name=tool_name,
            request=jsonable_encoder(params),
            success=True,
        )
        context.db.add(call)

        try:
            payload: BaseModel = definition.input_model(**params) if definition.input_model is not None else EmptyToolInput()
            response = await definition.handler(payload, context)
            if definition.output_model is not None:
                response = definition.output_model.model_validate(response)
            encoded = jsonable_encoder(_dump(response))
            call.response = encoded
            return encoded
        except Exception as exc:
            call.success = False
            call.error = str(exc)
            raise
        finally:
            call.duration_ms = int((perf_counter() - started) * 1000)
            await context.db.flush()


def _dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value
