from __future__ import annotations
from app.agents.common.base import BaseAgent
from app.agents.common.registry import agent_registry
from app.agents.nd_control_agent import config
from app.agents.nd_control_agent.graph import build_graph
from app.agents.nd_control_agent.schemas import NDControlInput, NDControlResult
from app.agents.nd_control_agent.tools import TOOL_NAMES
from app.models.enums import ConfidenceLevel
from app.core.logging import get_logger
logger = get_logger(__name__)

@agent_registry.register
class NDControlAgent(BaseAgent):
    agent_id = config.AGENT_ID
    name = config.AGENT_NAME
    version = config.AGENT_VERSION
    allowed_tools = TOOL_NAMES
    def __init__(self) -> None:
        self._graph = build_graph()
    async def run(self, payload: dict) -> NDControlResult:
        data = NDControlInput(**payload)
        if payload.get("service") is None or payload.get("current_user") is None:
            return NDControlResult(
                agent_id=self.agent_id,
                status="failed",
                summary="Для запуска агента нужен backend-контекст заявки, пользователя и БД",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=True,
                warnings=["Агент должен запускаться через сервис заявки ND change или orchestrator с контекстом БД"],
            )
        final_state = await self._graph.ainvoke(
            {
                "task_id": data.task_id,
                "document_ids": data.document_ids,
                "user_id": data.user_id,
                "change_request_id": str(data.change_request_id) if data.change_request_id else None,
                "reason": data.reason,
                "change_text": data.change_text,
                "service": payload.get("service"),
                "current_user": payload.get("current_user"),
                "findings": [],
                "warnings": [],
            }
        )
        return NDControlResult(
            agent_id=self.agent_id,
            status=final_state.get("status", "completed"),
            summary=final_state.get("summary", ""),
            findings=final_state.get("findings", []),
            data_confidence=ConfidenceLevel.HIGH if final_state.get("confidence", 0) >= 0.8 else ConfidenceLevel.MEDIUM,
            requires_human_review=final_state.get("requires_human_review", False),
            change_request_id=data.change_request_id,
            selected_document=final_state.get("selected_document"),
            confidence=final_state.get("confidence"),
            target_locations=final_state.get("target_locations", []),
            diff=final_state.get("diff", []),
            related_documents=final_state.get("related_documents", []),
            draft_file=final_state.get("draft_file"),
            change_notice_file=final_state.get("change_notice_file"),
            approval_recipients=final_state.get("approval_recipients", []),
            warnings=final_state.get("warnings", []),
            requires_user_review=final_state.get("requires_user_review", True),
        )

async def run_nd_control_agent(task_id: str) -> NDControlResult:
    logger.info("nd_control.run", task_id=task_id)
    agent = NDControlAgent()
    return await agent.run({"task_id": task_id, "document_ids": []})
