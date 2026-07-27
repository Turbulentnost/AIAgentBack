"""Ответы API для slot preview/detail с ошибками."""

from __future__ import annotations

from app.core.logging import get_logger
from app.schemas.meeting import (
    MeetingAgentSlotDetailRead,
    MeetingAgentSlotPreviewRead,
    MeetingAttendeeRead,
    MeetingSlotConflictRead,
)
from app.services.meeting_slot import format_slot_label, slot_duration_minutes

logger = get_logger(__name__)


def agent_slot_detail_error(
    memo_ref_key: str,
    *,
    slot_start: str,
    slot_end: str,
    message: str,
    error_stage: str = "unknown",
) -> MeetingAgentSlotDetailRead:
    logger.warning(
        "meeting.slot_detail.error",
        memo_ref_key=memo_ref_key,
        error_stage=error_stage,
        message=message,
    )
    duration = slot_duration_minutes(slot_start, slot_end)
    return MeetingAgentSlotDetailRead(
        memo_ref_key=memo_ref_key,
        slot_start=slot_start,
        slot_end=slot_end,
        slot_label=format_slot_label(slot_start, slot_end),
        duration_minutes=duration,
        participants=[],
        slot_available=False,
        reschedule_recommendations=[],
        error=message,
        error_stage=error_stage,
    )


def agent_slot_preview_error(
    memo_ref_key: str,
    *,
    message: str,
    duration_minutes: int | None = None,
    attendees: list[MeetingAttendeeRead] | None = None,
    missing_emails: list[str] | None = None,
    error_stage: str = "unknown",
    conflicts: list[MeetingSlotConflictRead] | None = None,
    preview_note: str | None = None,
) -> MeetingAgentSlotPreviewRead:
    logger.warning(
        "meeting.slot_preview.error",
        memo_ref_key=memo_ref_key,
        error_stage=error_stage,
        message=message,
    )
    return MeetingAgentSlotPreviewRead(
        memo_ref_key=memo_ref_key,
        duration_minutes=duration_minutes,
        attendees=attendees or [],
        missing_emails=missing_emails or [],
        error=message,
        error_stage=error_stage,
        conflicts=conflicts or [],
        preview_note=preview_note,
    )
