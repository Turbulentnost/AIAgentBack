"""Маппинг domain-объектов meeting agent в Pydantic-схемы API."""

from __future__ import annotations

from typing import Any

from app.agents.meeting_agent.backend import (
    InviteDraft,
    MeetingMemo,
    MeetingQuorumSlot,
    MeetingRoomOption,
    MeetingSlot,
    MeetingSlotConflict,
)
from app.models.meeting_registry import MeetingRegistryEntry
from app.schemas.meeting import (
    MeetingAttendeeRead,
    MeetingInviteDraftRead,
    MeetingMemoRead,
    MeetingQuorumSlotRead,
    MeetingRegistryItemRead,
    MeetingRegistryStageRead,
    MeetingRoomRead,
    MeetingSlotBlockingEventRead,
    MeetingSlotConflictRead,
    MeetingSlotCoverageRead,
    MeetingSlotParticipantStatusRead,
    MeetingSlotRead,
)
from app.services.meeting_attendee_priority import (
    PRIORITY_DIRECTOR,
    PRIORITY_INITIATOR,
    PRIORITY_MANAGER,
    priority_role_label,
)
from app.services.meeting_slot import format_event_time_display, format_slot_label

_VALID_MOVABILITY = frozenset({"high", "medium", "low"})
_VALID_MOVABILITY_REASON = frozenset({
    "tentative",
    "busy",
    "oof",
    "protected_subject",
    "unknown_interval",
})
_VALID_CONFLICT_SOURCE = frozenset({"calendar", "freebusy", "interval", "company_calendar"})


def memo_read(memo: MeetingMemo) -> MeetingMemoRead:
    return MeetingMemoRead(
        ref_key=memo.ref_key,
        number=memo.number,
        date=memo.date,
        subject=memo.subject,
        meeting_type=memo.meeting_type,
        participant_fio=memo.participant_fio,
    )


def slot_read(item: MeetingSlot) -> MeetingSlotRead:
    return MeetingSlotRead(start=item.start, end=item.end, confidence=item.confidence)


def email_roles_from_attendees(attendees: list[MeetingAttendeeRead]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for attendee in attendees:
        if attendee.email and attendee.role:
            roles[attendee.email] = attendee.role
    return roles


def attendee_weights_from_attendees(attendees: list[MeetingAttendeeRead]) -> dict[str, float]:
    return {
        attendee.email: attendee.weight
        for attendee in attendees
        if attendee.email
    }


def leadership_required_emails(attendees: list[MeetingAttendeeRead]) -> list[str]:
    leadership_roles = {PRIORITY_INITIATOR, PRIORITY_MANAGER, PRIORITY_DIRECTOR}
    return [
        attendee.email
        for attendee in attendees
        if attendee.email and attendee.role in leadership_roles
    ]


def attendee_meta_by_email(
    attendees: list[MeetingAttendeeRead],
) -> dict[str, MeetingAttendeeRead]:
    return {item.email: item for item in attendees if item.email}


def attendee_names_for_emails(
    emails: list[str],
    attendees: list[MeetingAttendeeRead],
) -> list[str]:
    meta = attendee_meta_by_email(attendees)
    names: list[str] = []
    for email in emails:
        attendee = meta.get(email)
        names.append(attendee.fio if attendee else email)
    return names


def coverage_read(item: MeetingQuorumSlot) -> MeetingSlotCoverageRead:
    return MeetingSlotCoverageRead(
        free=item.free_count,
        total=item.total_count,
        ratio=item.coverage_ratio,
        weighted_ratio=item.weighted_coverage_ratio,
        required_ok=item.required_ok,
    )


def quorum_slot_is_fully_free(item: MeetingQuorumSlot) -> bool:
    """Quorum нашёл слот, где свободны все участники — это не partial."""
    if not item.required_ok:
        return False
    if item.total_count <= 0:
        return False
    if item.free_count != item.total_count:
        return False
    if item.busy_attendees:
        return False
    if item.conflicts:
        return False
    return item.coverage_ratio >= 1.0 or item.free_count == item.total_count


def event_label_for_record(
    *,
    event_subject: str | None,
    event_start: str | None,
    event_end: str | None,
) -> str | None:
    subject = str(event_subject or "").strip()
    if subject:
        return subject
    if event_start and event_end:
        return "Занят"
    return None


def _normalize_event_fields(record: dict[str, Any]) -> dict[str, Any]:
    movability = record.get("movability") or "medium"
    if movability not in _VALID_MOVABILITY:
        movability = "medium"
    movability_reason = record.get("movability_reason")
    if movability_reason not in _VALID_MOVABILITY_REASON:
        movability_reason = None
    source = record.get("source")
    if source not in _VALID_CONFLICT_SOURCE:
        source = None
    raw_start = record.get("event_start")
    raw_end = record.get("event_end")
    event_start_label, event_end_label = format_event_time_display(
        str(raw_start) if raw_start else None,
        str(raw_end) if raw_end else None,
    )
    event_time_label = (
        format_slot_label(str(raw_start), str(raw_end))
        if raw_start and raw_end
        else None
    )
    hint_start = record.get("reschedule_hint_start")
    hint_end = record.get("reschedule_hint_end")
    hint_label = (
        format_slot_label(str(hint_start), str(hint_end))
        if hint_start and hint_end
        else None
    )
    return {
        "movability": movability,
        "movability_reason": movability_reason,
        "source": source,
        "raw_start": raw_start,
        "raw_end": raw_end,
        "event_start_label": event_start_label,
        "event_end_label": event_end_label,
        "event_time_label": event_time_label,
        "hint_start": hint_start,
        "hint_end": hint_end,
        "hint_label": hint_label,
    }


def conflict_read(
    conflict: MeetingSlotConflict,
    *,
    attendees: list[MeetingAttendeeRead],
) -> MeetingSlotConflictRead:
    meta = attendee_meta_by_email(attendees).get(conflict.email)
    normalized = _normalize_event_fields(
        {
            "movability": conflict.movability,
            "movability_reason": conflict.movability_reason,
            "source": conflict.source,
            "event_start": conflict.event_start,
            "event_end": conflict.event_end,
            "reschedule_hint_start": conflict.reschedule_hint_start,
            "reschedule_hint_end": conflict.reschedule_hint_end,
        }
    )
    return MeetingSlotConflictRead(
        fio=conflict.fio or (meta.fio if meta else None),
        email=conflict.email,
        role=conflict.role or (meta.role if meta else None),
        role_label=priority_role_label(conflict.role or (meta.role if meta else "")),
        event_start=normalized["event_start_label"],
        event_end=normalized["event_end_label"],
        event_subject=conflict.event_subject,
        event_label=event_label_for_record(
            event_subject=conflict.event_subject,
            event_start=conflict.event_start,
            event_end=conflict.event_end,
        ),
        event_time_label=normalized["event_time_label"],
        busy_type=conflict.busy_type,
        movability=normalized["movability"],
        movability_reason=normalized["movability_reason"],
        source=normalized["source"],
        can_auto_reschedule=conflict.can_auto_reschedule,
        reschedule_hint_start=conflict.reschedule_hint_start,
        reschedule_hint_end=conflict.reschedule_hint_end,
        reschedule_hint_label=normalized["hint_label"],
    )


def blocking_event_read(record: dict[str, Any]) -> MeetingSlotBlockingEventRead:
    normalized = _normalize_event_fields(record)
    raw_start = normalized["raw_start"]
    raw_end = normalized["raw_end"]
    return MeetingSlotBlockingEventRead(
        event_start=normalized["event_start_label"],
        event_end=normalized["event_end_label"],
        event_subject=record.get("event_subject"),
        event_label=event_label_for_record(
            event_subject=record.get("event_subject"),
            event_start=str(raw_start) if raw_start else None,
            event_end=str(raw_end) if raw_end else None,
        ),
        event_time_label=normalized["event_time_label"],
        organizer=record.get("organizer"),
        busy_type=record.get("busy_type"),
        movability=normalized["movability"],
        movability_reason=normalized["movability_reason"],
        source=normalized["source"],
        reschedule_hint_start=normalized["hint_start"],
        reschedule_hint_end=normalized["hint_end"],
        reschedule_hint_label=normalized["hint_label"],
    )


def participant_status_read(
    item: dict[str, Any],
    *,
    attendees: list[MeetingAttendeeRead],
) -> MeetingSlotParticipantStatusRead:
    email = item.get("email")
    meta = attendee_meta_by_email(attendees).get(email) if email else None
    role = str(item.get("role") or (meta.role if meta else "participant"))
    status = item.get("status") or "unknown"
    if status not in {"free", "busy", "unknown"}:
        status = "unknown"
    return MeetingSlotParticipantStatusRead(
        fio=str(item.get("fio") or (meta.fio if meta else email or "—")),
        email=email,
        role=role,
        role_label=priority_role_label(role),
        status=status,
        blocking_events=[
            blocking_event_read(record)
            for record in item.get("blocking_events") or []
        ],
        calendar_access_error=item.get("calendar_access_error"),
    )


def quorum_slot_read(
    item: MeetingQuorumSlot,
    *,
    attendees: list[MeetingAttendeeRead],
) -> MeetingQuorumSlotRead:
    return MeetingQuorumSlotRead(
        slot=slot_read(
            MeetingSlot(start=item.start, end=item.end, confidence=item.confidence)
        ),
        slot_label=format_slot_label(item.start, item.end),
        coverage=coverage_read(item),
        conflicts=[conflict_read(conflict, attendees=attendees) for conflict in item.conflicts],
        free_attendees=item.free_attendees,
        busy_attendees=item.busy_attendees,
        free_attendee_names=attendee_names_for_emails(item.free_attendees, attendees),
        busy_attendee_names=attendee_names_for_emails(item.busy_attendees, attendees),
        verified=item.verified,
        impact_score=item.impact_score,
        busy_weight_cost=item.busy_weight_cost,
        reschedule_count=item.reschedule_count,
        easy_reschedule_count=item.easy_reschedule_count,
        low_movability_count=item.low_movability_count,
    )


def room_read(item: MeetingRoomOption) -> MeetingRoomRead:
    return MeetingRoomRead(name=item.name, email=item.email, available=item.available)


def registry_item_read(entry: MeetingRegistryEntry) -> MeetingRegistryItemRead:
    return MeetingRegistryItemRead(
        ref_key=entry.memo_ref_key,
        memo_number=entry.memo_number,
        title=entry.title,
        subject=entry.subject,
        location=entry.location,
        initiator_name=entry.initiator_name,
        manager_name=entry.manager_name,
        participants_count=entry.participants_count,
        slot_start=entry.slot_start.isoformat() if entry.slot_start else None,
        slot_end=entry.slot_end.isoformat() if entry.slot_end else None,
        stage=MeetingRegistryStageRead(entry.stage.value),
        invitations_sent_at=entry.invitations_sent_at.isoformat(),
        approved_at=entry.approved_at.isoformat() if entry.approved_at else None,
        protocol_number=entry.protocol_number,
        outlook_item_id=entry.outlook_item_id,
        outlook_changekey=entry.outlook_changekey,
        outlook_meeting_url=entry.outlook_meeting_url,
        updated_at=entry.updated_at.isoformat(),
    )


def invite_read(item: InviteDraft) -> MeetingInviteDraftRead:
    return MeetingInviteDraftRead(
        subject=item.subject,
        start=item.start,
        end=item.end,
        location=item.location,
        attendees=item.attendees,
        body=item.body,
    )
