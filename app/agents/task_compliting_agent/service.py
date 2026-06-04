from __future__ import annotations

from app.agents.common.base import BaseAgent
from app.agents.common.registry import agent_registry
from app.agents.common.schemas import AgentResult
from app.agents.task_compliting_agent import config
from app.agents.task_compliting_agent.graph import build_graph
from app.agents.task_compliting_agent.schemas import TaskCompletingAssessment, TaskCompletingInput
from app.agents.task_compliting_agent.tools import TOOL_NAMES
from app.core.logging import get_logger
from app.models.enums import ConfidenceLevel, FindingSeverity
from app.schemas.task import Finding

logger = get_logger(__name__)


def _severity_for_status(status: str) -> FindingSeverity:
    if status == config.STATUS_IRRELEVANT:
        return FindingSeverity.HIGH
    if status == config.STATUS_PARTIALLY_RELEVANT:
        return FindingSeverity.MEDIUM
    if status == config.STATUS_FILE_REQUIRED:
        return FindingSeverity.MEDIUM
    return FindingSeverity.INFO


def _confidence_level(value: str) -> ConfidenceLevel:
    try:
        return ConfidenceLevel(value)
    except ValueError:
        return ConfidenceLevel.MEDIUM


@agent_registry.register
class TaskCompletingAgent(BaseAgent):
    agent_id = config.AGENT_ID
    name = config.AGENT_NAME
    version = config.AGENT_VERSION
    allowed_tools = TOOL_NAMES

    def __init__(self) -> None:
        self._graph = build_graph()

    async def run(self, payload: dict) -> AgentResult:
        data = TaskCompletingInput(**payload)
        final_state = await self._graph.ainvoke(
            {
                "task_id": data.task_id,
                "document_ids": data.document_ids,
                "user_id": data.user_id,
                "task_name": data.task_name,
                "comment_text": data.comment_text,
                "findings": [],
            }
        )
        assessment = TaskCompletingAssessment.model_validate(
            final_state.get("assessment") or {},
        )
        findings = [
            Finding(
                type="task_comment_assessment",
                severity=_severity_for_status(assessment.status),
                description=assessment.conclusion,
                source=assessment.status,
            )
        ]
        return AgentResult(
            agent_id=self.agent_id,
            status="completed",
            summary=final_state.get("summary", assessment.conclusion),
            findings=findings,
            data_confidence=_confidence_level(str(final_state.get("data_confidence", "medium"))),
            requires_human_review=bool(final_state.get("requires_human_review", False)),
        )


async def run_task_compliting_agent(
    task_id: str,
    task_name: str,
    comment_text: str = "",
    *,
    execution_result: dict[str, str | None] | None = None,
) -> AgentResult:
    logger.info("task_compliting.run", task_id=task_id)
    agent = TaskCompletingAgent()
    payload: dict = {
        "task_id": task_id,
        "task_name": task_name,
        "document_ids": [],
    }
    if execution_result is not None:
        payload["execution_result"] = execution_result
    else:
        payload["comment_text"] = comment_text
    return await agent.run(payload)
