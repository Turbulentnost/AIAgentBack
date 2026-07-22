from __future__ import annotations

import uuid

from app.models.enums import ScheduledMeetingStatus
from app.models.scheduled_meeting import ScheduledMeeting
from app.schemas.scheduled_meeting import ScheduledMeetingParticipantCreate


def is_scheduled_meeting_structure_locked(meeting: ScheduledMeeting) -> bool:
    return meeting.status != ScheduledMeetingStatus.CREATED or bool(meeting.outlook_series_id)


def merge_scheduled_meeting_participants(
    participants: list[ScheduledMeetingParticipantCreate],
    *,
    manager_position_id: uuid.UUID,
    responsible_position_id: uuid.UUID,
) -> list[ScheduledMeetingParticipantCreate]:
    """Руководитель и ответственный всегда входят в состав участников."""
    payload_by_id = {
        participant.position_id: participant
        for participant in participants
        if participant.position_id is not None
    }
    ordered_ids: list[uuid.UUID] = []
    for position_id in (manager_position_id, responsible_position_id):
        if position_id not in ordered_ids:
            ordered_ids.append(position_id)
    for position_id in payload_by_id:
        if position_id not in ordered_ids:
            ordered_ids.append(position_id)

    merged: list[ScheduledMeetingParticipantCreate] = []
    for index, position_id in enumerate(ordered_ids):
        source = payload_by_id.get(position_id)
        merged.append(
            ScheduledMeetingParticipantCreate(
                position_id=position_id,
                sort_order=source.sort_order if source is not None and source.sort_order else index,
                is_required=source.is_required if source is not None else True,
            )
        )
    return merged


def protected_participant_position_ids(meeting: ScheduledMeeting) -> set[uuid.UUID]:
    return {meeting.manager_position_id, meeting.responsible_position_id}
