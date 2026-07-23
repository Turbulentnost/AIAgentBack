from __future__ import annotations

import uuid

from app.models.enums import ScheduledMeetingStatus
from app.models.scheduled_meeting import ScheduledMeeting
from app.schemas.scheduled_meeting import ScheduledMeetingParticipantCreate


def is_scheduled_meeting_structure_locked(meeting: ScheduledMeeting) -> bool:
    return meeting.status != ScheduledMeetingStatus.CREATED or bool(meeting.outlook_series_id)


def participant_user_id(participant: ScheduledMeetingParticipantCreate) -> uuid.UUID | None:
    return participant.user_id


def merge_scheduled_meeting_participants(
    participants: list[ScheduledMeetingParticipantCreate],
    *,
    manager_user_id: uuid.UUID,
    responsible_user_id: uuid.UUID,
) -> list[ScheduledMeetingParticipantCreate]:
    """Руководитель и ответственный всегда входят в состав участников."""
    payload_by_user = {
        participant.user_id: participant
        for participant in participants
        if participant.user_id is not None
    }
    ordered_ids: list[uuid.UUID] = []
    for user_id in (manager_user_id, responsible_user_id):
        if user_id not in ordered_ids:
            ordered_ids.append(user_id)
    for user_id in payload_by_user:
        if user_id not in ordered_ids:
            ordered_ids.append(user_id)

    merged: list[ScheduledMeetingParticipantCreate] = []
    for index, user_id in enumerate(ordered_ids):
        source = payload_by_user.get(user_id)
        merged.append(
            ScheduledMeetingParticipantCreate(
                user_id=user_id,
                person_fio=source.person_fio if source is not None else None,
                person_email=source.person_email if source is not None else None,
                position_id=source.position_id if source is not None else None,
                sort_order=source.sort_order if source is not None and source.sort_order else index,
                is_required=source.is_required if source is not None else True,
            )
        )
    return merged


def protected_participant_user_ids(meeting: ScheduledMeeting) -> set[uuid.UUID]:
    protected: set[uuid.UUID] = set()
    if meeting.manager_user_id is not None:
        protected.add(meeting.manager_user_id)
    if meeting.responsible_user_id is not None:
        protected.add(meeting.responsible_user_id)
    return protected
