from __future__ import annotations

from app.agents.common.base import BaseAgent
from app.agents.common.registry import agent_registry
from app.agents.meeting_agent import config
from app.agents.meeting_agent.graph import build_graph
from app.agents.meeting_agent.schemas import MeetingInput, MeetingResult
from app.agents.meeting_agent.tools import TOOL_NAMES
from app.core.logging import get_logger
from app.models.enums import ConfidenceLevel

logger = get_logger(__name__)


@agent_registry.register
class MeetingAgent(BaseAgent):
    agent_id = config.AGENT_ID
    name = config.AGENT_NAME
    purpose = config.AGENT_PURPOSE
    version = config.AGENT_VERSION
    allowed_tools = TOOL_NAMES

    def __init__(self) -> None:
        self._graph = build_graph()

    async def run(self, payload: dict) -> MeetingResult:
        data = MeetingInput(**payload)
        if payload.get("backend") is None or payload.get("current_user") is None:
            return MeetingResult(
                agent_id=self.agent_id,
                status="failed",
                summary="Для запуска агента нужен MeetingBackend, пользователь и сессия БД",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=True,
                warnings=["Передайте backend=MeetingBackend(db) и current_user при запуске агента"],
            )
        final_state = await self._graph.ainvoke(
            {
                "task_id": data.task_id,
                "document_ids": data.document_ids,
                "user_id": data.user_id,
                "memo_ref_key": str(data.memo_ref_key) if data.memo_ref_key else None,
                "memo_number": data.memo_number,
                "meeting_type": data.meeting_type,
                "subject": data.subject,
                "planned_start": data.planned_start.isoformat() if data.planned_start else None,
                "duration_minutes": data.duration_minutes,
                "participant_fio": data.participant_fio,
                "room_name": data.room_name,
                "initiator_comment": data.initiator_comment,
                "backend": payload.get("backend"),
                "current_user": payload.get("current_user"),
                "findings": [],
                "warnings": [],
            }
        )
        selected_slot = final_state.get("selected_slot") or {}
        confidence = selected_slot.get("confidence", 0.0)
        return MeetingResult(
            agent_id=self.agent_id,
            status=final_state.get("status", "completed"),
            summary=final_state.get("summary", ""),
            findings=final_state.get("findings", []),
            data_confidence=ConfidenceLevel.HIGH if confidence >= 0.8 else ConfidenceLevel.MEDIUM,
            requires_human_review=final_state.get("requires_human_review", False),
            memo_ref_key=data.memo_ref_key,
            memo=final_state.get("memo"),
            validation_issues=final_state.get("validation_issues", []),
            participants=final_state.get("participants", []),
            suggested_slots=final_state.get("suggested_slots", []),
            selected_slot=final_state.get("selected_slot"),
            available_rooms=final_state.get("available_rooms", []),
            selected_room=final_state.get("selected_room"),
            invite_draft=final_state.get("invite_draft"),
            warnings=final_state.get("warnings", []),
            requires_user_review=final_state.get("requires_user_review", True),
        )


async def run_meeting_agent(task_id: str) -> MeetingResult:
    logger.info("meeting.run", task_id=task_id)
    agent = MeetingAgent()
    return await agent.run({"task_id": task_id, "document_ids": []})
