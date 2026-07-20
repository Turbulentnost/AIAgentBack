from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tasks_agent.config import DEFAULT_PORUCHENIYA_LIMIT
from app.agents.tasks_agent.tools import TOOL_NAMES
from app.models.user import User
from app.tools.executor import ToolExecutor, ToolExecutionError
from app.tools.schemas import ToolContext


class TasksBackendError(ValueError):
    pass


class TasksBackend:
    """Оркестрация инструментов 1С для узлов графа tasks_agent."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        tool_executor: ToolExecutor | None = None,
        agent_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
    ) -> None:
        self.db = db
        self.tool_executor = tool_executor or ToolExecutor()
        self.agent_id = agent_id
        self.task_id = task_id

    async def load_porucheniya(
        self,
        *,
        period_start: str | None = None,
        period_end: str | None = None,
        limit: int = DEFAULT_PORUCHENIYA_LIMIT,
        current_user: User,
    ) -> dict[str, Any]:
        try:
            payload = await self._invoke(
                "get_porucheniya",
                {
                    "period_start": period_start,
                    "period_end": period_end,
                    "limit": limit,
                },
                current_user=current_user,
            )
        except ToolExecutionError as exc:
            raise TasksBackendError(str(exc)) from exc
        except Exception as exc:
            raise TasksBackendError(str(exc)) from exc
        return payload

    async def _invoke(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        current_user: User,
    ) -> dict[str, Any]:
        context = ToolContext(
            db=self.db,
            user=current_user,
            agent_id=self.agent_id,
            task_id=self.task_id,
        )
        result = await self.tool_executor.invoke(
            tool_name=tool_name,
            params=params,
            context=context,
            allowed_tools=TOOL_NAMES,
        )
        if not isinstance(result, dict):
            raise TasksBackendError(f"Инструмент {tool_name} вернул неожиданный ответ")
        return result
