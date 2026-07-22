from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, time

from app.models.scheduled_meeting import ScheduledMeeting
from app.schemas.scheduled_meeting import (
    ScheduledMeetingParticipantCreate,
    ScheduledMeetingRecurrencePayload,
    ScheduledMeetingUpdate,
)
from app.services.scheduled_meeting_roles import is_scheduled_meeting_structure_locked


@dataclass(frozen=True)
class SeriesUpdateChangeSet:
    new_series_end_date: date
    series_end_changed: bool
    comment_changed: bool
    meeting_category_changed: bool
    manager_changed: bool
    responsible_changed: bool
    new_meeting_category_id: uuid.UUID | None
    new_manager_position_id: uuid.UUID | None
    new_responsible_position_id: uuid.UUID | None
    participants_changed: bool
    participants_added: tuple[uuid.UUID, ...]
    participants_removed: tuple[uuid.UUID, ...]
    new_participants: tuple[ScheduledMeetingParticipantCreate, ...]
    unsupported_fields: tuple[str, ...]


def resolved_update_series_end_date(
    meeting: ScheduledMeeting,
    payload: ScheduledMeetingUpdate,
) -> date:
    if payload.series_end_date is not None:
        return payload.series_end_date
    if payload.recurrence is not None and payload.recurrence.series_end_date is not None:
        return payload.recurrence.series_end_date
    return meeting.series_end_date


def _participant_position_ids(meeting: ScheduledMeeting) -> list[uuid.UUID]:
    return [
        participant.position_id
        for participant in sorted(meeting.participants, key=lambda item: item.sort_order)
    ]


def _payload_participant_position_ids(
    participants: list[ScheduledMeetingParticipantCreate],
) -> list[uuid.UUID]:
    return [item.position_id for item in participants if item.position_id is not None]


def _normalize_time(value: time) -> time:
    return value.replace(second=0, microsecond=0)


def _recurrence_schedule_changed(
    meeting: ScheduledMeeting,
    recurrence: ScheduledMeetingRecurrencePayload,
) -> bool:
    if recurrence.frequency != meeting.frequency:
        return True
    if recurrence.interval != meeting.interval:
        return True
    if _normalize_time(recurrence.time_local) != _normalize_time(meeting.time_local):
        return True
    if recurrence.duration_minutes != meeting.duration_minutes:
        return True
    if recurrence.monthly_mode != meeting.monthly_mode:
        return True
    if recurrence.day_of_month != meeting.day_of_month:
        return True
    if recurrence.weekday != meeting.weekday:
        return True
    if recurrence.weekday_position != meeting.weekday_position:
        return True
    if recurrence.series_start_date is not None and recurrence.series_start_date != meeting.series_start_date:
        return True
    return False


def _unsupported_update_fields(
    meeting: ScheduledMeeting,
    payload: ScheduledMeetingUpdate,
) -> list[str]:
    unsupported: list[str] = []
    structure_locked = is_scheduled_meeting_structure_locked(meeting)

    if payload.title is not None and payload.title.strip() != meeting.title.strip():
        unsupported.append("название")
    if payload.meeting_type is not None and payload.meeting_type != meeting.meeting_type:
        unsupported.append("тип")
    if payload.status is not None and payload.status != meeting.status:
        unsupported.append("статус")
    if payload.series_start_date is not None and payload.series_start_date != meeting.series_start_date:
        unsupported.append("дата начала серии")
    if payload.recurrence is not None and _recurrence_schedule_changed(meeting, payload.recurrence):
        unsupported.append("периодичность")

    if structure_locked:
        if (
            payload.meeting_category_id is not None
            and payload.meeting_category_id != meeting.meeting_category_id
        ):
            unsupported.append("вид совещания")
        if (
            payload.manager_position_id is not None
            and payload.manager_position_id != meeting.manager_position_id
        ):
            unsupported.append("руководитель")
        if (
            payload.responsible_position_id is not None
            and payload.responsible_position_id != meeting.responsible_position_id
        ):
            unsupported.append("ответственный")

    return unsupported


def _role_field_changes(
    meeting: ScheduledMeeting,
    payload: ScheduledMeetingUpdate,
) -> tuple[bool, bool, bool, uuid.UUID | None, uuid.UUID | None, uuid.UUID | None]:
    if is_scheduled_meeting_structure_locked(meeting):
        return False, False, False, None, None, None

    meeting_category_changed = (
        payload.meeting_category_id is not None
        and payload.meeting_category_id != meeting.meeting_category_id
    )
    manager_changed = (
        payload.manager_position_id is not None
        and payload.manager_position_id != meeting.manager_position_id
    )
    responsible_changed = (
        payload.responsible_position_id is not None
        and payload.responsible_position_id != meeting.responsible_position_id
    )
    return (
        meeting_category_changed,
        manager_changed,
        responsible_changed,
        payload.meeting_category_id if meeting_category_changed else None,
        payload.manager_position_id if manager_changed else None,
        payload.responsible_position_id if responsible_changed else None,
    )


def _participant_update_change(
    meeting: ScheduledMeeting,
    payload: ScheduledMeetingUpdate,
) -> tuple[bool, tuple[uuid.UUID, ...], tuple[uuid.UUID, ...], tuple[ScheduledMeetingParticipantCreate, ...]]:
    if payload.participants is None:
        return False, (), (), ()

    current_ids = _participant_position_ids(meeting)
    new_items = tuple(payload.participants)
    new_ids = _payload_participant_position_ids(payload.participants)
    if current_ids == new_ids:
        return False, (), (), ()

    current_set = set(current_ids)
    new_set = set(new_ids)
    added = tuple(position_id for position_id in new_ids if position_id not in current_set)
    removed = tuple(position_id for position_id in current_ids if position_id not in new_set)
    return True, added, removed, new_items


def _resolved_comment(payload: ScheduledMeetingUpdate) -> str | None:
    if payload.comment is None:
        return None
    text = payload.comment.strip()
    return text or None


def build_series_update_change_set(
    meeting: ScheduledMeeting,
    payload: ScheduledMeetingUpdate,
) -> SeriesUpdateChangeSet:
    new_end = resolved_update_series_end_date(meeting, payload)
    current_comment = (meeting.payload or {}).get("comment")
    if isinstance(current_comment, str):
        current_comment = current_comment.strip() or None
    else:
        current_comment = None

    new_comment = _resolved_comment(payload)
    comment_changed = payload.comment is not None and new_comment != current_comment

    participants_changed, participants_added, participants_removed, new_participants = (
        _participant_update_change(meeting, payload)
    )
    unsupported = list(_unsupported_update_fields(meeting, payload))
    (
        meeting_category_changed,
        manager_changed,
        responsible_changed,
        new_meeting_category_id,
        new_manager_position_id,
        new_responsible_position_id,
    ) = _role_field_changes(meeting, payload)

    return SeriesUpdateChangeSet(
        new_series_end_date=new_end,
        series_end_changed=new_end != meeting.series_end_date,
        comment_changed=comment_changed,
        meeting_category_changed=meeting_category_changed,
        manager_changed=manager_changed,
        responsible_changed=responsible_changed,
        new_meeting_category_id=new_meeting_category_id,
        new_manager_position_id=new_manager_position_id,
        new_responsible_position_id=new_responsible_position_id,
        participants_changed=participants_changed,
        participants_added=participants_added,
        participants_removed=participants_removed,
        new_participants=new_participants,
        unsupported_fields=tuple(unsupported),
    )
