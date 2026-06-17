from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.agents.common.schemas import AgentResult, BaseAgentInput, Finding


class MeetingInput(BaseAgentInput):
    memo_ref_key: uuid.UUID | None = None
    memo_number: str | None = None
    meeting_type: str | None = None
    subject: str | None = None
    planned_start: datetime | None = None
    duration_minutes: int | None = None
    participant_fio: list[str] = Field(default_factory=list)
    room_name: str | None = None
    initiator_comment: str | None = None


class MeetingStructuredResult(AgentResult):
    memo_ref_key: uuid.UUID | None = None
    memo: dict[str, Any] | None = None
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)
    participants: list[dict[str, Any]] = Field(default_factory=list)
    suggested_slots: list[dict[str, Any]] = Field(default_factory=list)
    selected_slot: dict[str, Any] | None = None
    available_rooms: list[dict[str, Any]] = Field(default_factory=list)
    selected_room: dict[str, Any] | None = None
    invite_draft: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    requires_user_review: bool = True


MeetingResult = MeetingStructuredResult
__all__ = ["MeetingInput", "MeetingResult", "MeetingStructuredResult", "AgentResult", "Finding"]
