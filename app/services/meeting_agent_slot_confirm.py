"""Подтверждение слота с конфликтами: флаги UI и перенос встреч."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.schemas.meeting import (
    MeetingAttendeeRead,
    MeetingAgentSlotApproveRequest,
    MeetingSlotBlockingEventRead,
    MeetingSlotParticipantStatusRead,
    MeetingSlotRoomStatusRead,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class SlotConflictRescheduleTarget:
    event_subject: str
    event_start: str
    event_end: str
    reschedule_hint_start: str
    reschedule_hint_end: str
    source: str | None = None


_RESCHEDULABLE_SOURCES = frozenset({"company_calendar", "calendar"})


def _event_reschedule_key(event: MeetingSlotBlockingEventRead) -> tuple[str, str, str]:
    return (
        str(event.event_start_iso or ""),
        str(event.event_end_iso or ""),
        str(event.event_subject or "").strip(),
    )


def collect_slot_conflict_reschedule_targets(
    participants: list[MeetingSlotParticipantStatusRead],
) -> list[SlotConflictRescheduleTarget]:
    """Уникальные конфликты с альтернативой переноса (один раз на встречу)."""
    seen: set[tuple[str, str, str]] = set()
    targets: list[SlotConflictRescheduleTarget] = []
    for participant in participants:
        if participant.role == "room" or participant.status != "busy":
            continue
        for event in participant.blocking_events:
            key = _event_reschedule_key(event)
            if not key[0] or not key[2] or key in seen:
                continue
            if event.source not in _RESCHEDULABLE_SOURCES:
                continue
            if not event.reschedule_hint_start or not event.reschedule_hint_end:
                continue
            seen.add(key)
            targets.append(
                SlotConflictRescheduleTarget(
                    event_subject=key[2],
                    event_start=key[0],
                    event_end=str(event.event_end_iso or ""),
                    reschedule_hint_start=event.reschedule_hint_start,
                    reschedule_hint_end=event.reschedule_hint_end,
                    source=event.source,
                )
            )
    return targets


def build_slot_confirm_state(
    participants: list[MeetingSlotParticipantStatusRead],
    *,
    room: MeetingSlotRoomStatusRead | None,
    slot_available: bool,
) -> tuple[bool, bool]:
    """(can_confirm, requires_reschedule) для кнопки «Согласовать и утвердить»."""
    del room
    targets = collect_slot_conflict_reschedule_targets(participants)
    if slot_available:
        return True, False
    if targets:
        return True, True
    return False, False


def fetch_reschedule_targets_for_slot_sync(
    *,
    slot_start: str,
    slot_end: str,
    attendee_details: list[MeetingAttendeeRead],
    participants_payload: list[MeetingSlotParticipantStatusRead] | None = None,
    company_calendar_cache_id: str | None = None,
) -> list[SlotConflictRescheduleTarget]:
    """Собирает цели переноса: из payload UI или повторной проверки слота на сервере."""
    if participants_payload:
        payload_targets = collect_slot_conflict_reschedule_targets(participants_payload)
        if payload_targets:
            logger.info(
                "meeting.agent_slot_approve.reschedule_targets_from_payload count=%d",
                len(payload_targets),
            )
            return payload_targets

    from app.services.meeting_mappers import participant_status_read
    from app.services.meeting_slot import parse_slot_datetime
    from app.tools.Outlook.find_meeting_slot import build_slot_participant_details
    from app.tools.Outlook.send_meeting_invite import load_config

    start_dt = parse_slot_datetime(slot_start)
    end_dt = parse_slot_datetime(slot_end)
    if start_dt is None or end_dt is None:
        return []

    attendee_payload = [
        {"fio": attendee.fio, "email": attendee.email, "role": attendee.role}
        for attendee in attendee_details
        if attendee.email
    ]
    if not attendee_payload:
        return []

    raw = build_slot_participant_details(
        config=load_config(),
        attendees=attendee_payload,
        slot_start=start_dt,
        slot_end=end_dt,
        include_company_calendar=True,
        manual_slot_check=True,
        light_reschedule_hints=False,
        company_calendar_cache_id=company_calendar_cache_id,
    )
    participants = [
        participant_status_read(item, attendees=attendee_details)
        for item in raw.get("participants") or []
    ]
    targets = collect_slot_conflict_reschedule_targets(participants)
    logger.info(
        "meeting.agent_slot_approve.reschedule_targets_from_slot_check count=%d",
        len(targets),
    )
    return targets


def fetch_reschedule_targets_for_approve_sync(
    payload: MeetingAgentSlotApproveRequest,
    attendee_details: list[MeetingAttendeeRead],
) -> list[SlotConflictRescheduleTarget]:
    return fetch_reschedule_targets_for_slot_sync(
        slot_start=payload.slot_start,
        slot_end=payload.slot_end,
        attendee_details=attendee_details,
        participants_payload=payload.participants,
        company_calendar_cache_id=payload.company_calendar_cache_id,
    )


def reschedule_slot_conflicts_sync(
    targets: list[SlotConflictRescheduleTarget],
    *,
    message: str,
) -> list[dict[str, Any]]:
    from app.tools.Outlook.reschedule_meeting import dispatch_reschedule_meeting

    results: list[dict[str, Any]] = []
    for target in targets:
        logger.info(
            "meeting.agent_slot_approve.reschedule subject=%s from=%s to=%s",
            target.event_subject,
            target.event_start,
            target.reschedule_hint_start,
        )
        payload = dispatch_reschedule_meeting(
            subject=target.event_subject,
            start=target.event_start,
            new_start=target.reschedule_hint_start,
            new_end=target.reschedule_hint_end,
            message=message,
            reschedule_scope="occurrence",
        )
        if payload.get("status") != "rescheduled":
            raise RuntimeError(
                f"Не удалось перенести «{target.event_subject}»: "
                f"{payload.get('error') or payload.get('status') or 'неизвестная ошибка'}"
            )
        results.append(payload)
    return results
