from __future__ import annotations

import uuid

from app.schemas.scheduled_meeting import ScheduledMeetingParticipantCreate
from app.services.scheduled_meeting_roles import merge_scheduled_meeting_participants


def test_merge_scheduled_meeting_participants_adds_roles_first() -> None:
    manager_id = uuid.uuid4()
    responsible_id = uuid.uuid4()
    extra_id = uuid.uuid4()

    merged = merge_scheduled_meeting_participants(
        [
            ScheduledMeetingParticipantCreate(position_id=extra_id, sort_order=5),
        ],
        manager_position_id=manager_id,
        responsible_position_id=responsible_id,
    )

    assert [item.position_id for item in merged] == [manager_id, responsible_id, extra_id]


def test_merge_scheduled_meeting_participants_deduplicates_roles() -> None:
    manager_id = uuid.uuid4()
    responsible_id = manager_id
    extra_id = uuid.uuid4()

    merged = merge_scheduled_meeting_participants(
        [
            ScheduledMeetingParticipantCreate(position_id=manager_id, sort_order=1),
            ScheduledMeetingParticipantCreate(position_id=extra_id, sort_order=2),
        ],
        manager_position_id=manager_id,
        responsible_position_id=responsible_id,
    )

    assert [item.position_id for item in merged] == [manager_id, extra_id]
