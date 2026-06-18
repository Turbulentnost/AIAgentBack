from __future__ import annotations

from app.agents.common.base import BaseAgent
from app.agents.common.registry import agent_registry
from app.agents.tasks_agent import config
from app.agents.tasks_agent.graph import build_graph
from app.agents.tasks_agent.schemas import TasksInput, TasksResult
from app.agents.tasks_agent.tools import TOOL_NAMES
from app.core.logging import get_logger
from app.models.enums import ConfidenceLevel

logger = get_logger(__name__)


@agent_registry.register
class TasksAgent(BaseAgent):
    agent_id = config.AGENT_ID
    name = config.AGENT_NAME
    purpose = config.AGENT_PURPOSE
    version = config.AGENT_VERSION
    allowed_tools = TOOL_NAMES

    def __init__(self) -> None:
        self._graph = build_graph()

    async def run(self, payload: dict) -> TasksResult:
        data = TasksInput(**payload)
        if payload.get("backend") is None or payload.get("current_user") is None:
            return TasksResult(
                agent_id=self.agent_id,
                status="failed",
                summary="Для запуска агента нужен TasksBackend, пользователь и сессия БД",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=True,
                warnings=["Передайте backend=TasksBackend(db) и current_user при запуске агента"],
            )
        final_state = await self._graph.ainvoke(
            {
                "task_id": data.task_id,
                "document_ids": data.document_ids,
                "user_id": data.user_id,
                "period_start": data.period_start.isoformat() if data.period_start else None,
                "period_end": data.period_end.isoformat() if data.period_end else None,
                "limit": data.limit,
                "backend": payload.get("backend"),
                "current_user": payload.get("current_user"),
                "findings": [],
                "warnings": [],
            }
        )
        return TasksResult(
            agent_id=self.agent_id,
            status=final_state.get("status", "completed"),
            summary=final_state.get("summary", ""),
            findings=final_state.get("findings", []),
            data_confidence=ConfidenceLevel.HIGH,
            requires_human_review=final_state.get("requires_human_review", False),
            period_start=data.period_start,
            period_end=data.period_end,
            porucheniya=final_state.get("porucheniya", []),
            priority_summary=final_state.get("priority_summary", {}),
            warnings=final_state.get("warnings", []),
            requires_user_review=final_state.get("requires_user_review", True),
        )


async def run_tasks_agent(task_id: str) -> TasksResult:
    logger.info("tasks.run", task_id=task_id)
    agent = TasksAgent()
    return await agent.run({"task_id": task_id, "document_ids": []})
