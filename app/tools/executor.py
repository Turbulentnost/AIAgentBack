from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from app.models.agent import ToolCall
from app.tools.registry import tool_registry
from app.tools.schemas import EmptyToolInput, ToolContext


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

        tool = tool_registry.get(tool_name)
        if tool is None or not tool.implemented:
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
            payload: BaseModel = tool.input_model(**params) if tool.input_model is not None else EmptyToolInput()
            response = await tool.execute(payload, context)
            if tool.output_model is not None:
                response = tool.output_model.model_validate(response)
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
