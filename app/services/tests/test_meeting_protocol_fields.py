from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.meeting_registry import MeetingRegistryEntry
from app.models.scheduled_meeting import ScheduledMeeting
from app.services.meeting_protocol_fields import (
    MEMO_BASIS_TYPE,
    PROTOCOL_BASIS_TYPE,
    build_protocol_creation_fields,
    is_series_entry,
    resolve_basis_for_protocol,
    resolve_next_meeting_date_for_series,
)


def test_is_series_entry() -> None:
    one_time = MeetingRegistryEntry(
        memo_ref_key="memo-1",
        invitations_sent_at=datetime.now(timezone.utc),
    )
    series = MeetingRegistryEntry(
        memo_ref_key="memo-2",
        invitations_sent_at=datetime.now(timezone.utc),
        scheduled_meeting_id=uuid.uuid4(),
    )

    assert is_series_entry(one_time) is False
    assert is_series_entry(series) is True


def test_resolve_basis_for_one_time_meeting() -> None:
    entry = MeetingRegistryEntry(
        memo_ref_key="memo-ref",
        invitations_sent_at=datetime.now(timezone.utc),
    )

    basis_key, basis_type = resolve_basis_for_protocol(
        entry,
        topic_key="topic-1",
        session=MagicMock(),
        config=MagicMock(),
    )

    assert basis_key == "memo-ref"
    assert basis_type == MEMO_BASIS_TYPE


def test_resolve_basis_for_series_uses_previous_protocol() -> None:
    entry = MeetingRegistryEntry(
        memo_ref_key=str(uuid.uuid4()),
        invitations_sent_at=datetime.now(timezone.utc),
        scheduled_meeting_id=uuid.uuid4(),
        slot_start=datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc),
    )

    with patch(
        "app.tools.onec.create_protocol.fetch_previous_protocol_by_topic",
        return_value={"Ref_Key": "prev-proto"},
    ):
        basis_key, basis_type = resolve_basis_for_protocol(
            entry,
            topic_key="topic-1",
            session=MagicMock(),
            config=MagicMock(),
        )

    assert basis_key == "prev-proto"
    assert basis_type == PROTOCOL_BASIS_TYPE


def test_resolve_next_meeting_date_for_series_returns_none_for_last_occurrence() -> None:
    meeting = MagicMock(spec=ScheduledMeeting)
    meeting.series_start_date = date(2026, 7, 1)
    meeting.series_end_date = date(2026, 7, 22)

    with patch(
        "app.services.scheduled_meeting_occurrences.resolve_series_occurrences",
        return_value=(
            [
                MagicMock(occurrence_date=date(2026, 7, 15), slot_start=datetime(2026, 7, 15, 10, 0)),
                MagicMock(occurrence_date=date(2026, 7, 22), slot_start=datetime(2026, 7, 22, 10, 0)),
            ],
            "rule",
        ),
    ), patch(
        "app.services.scheduled_meeting_occurrences.find_next_after",
        return_value=None,
    ):
        result = resolve_next_meeting_date_for_series(
            meeting,
            current_occurrence_date=date(2026, 7, 22),
        )

    assert result is None


def test_resolve_next_meeting_date_for_series_returns_next_date() -> None:
    meeting = MagicMock(spec=ScheduledMeeting)
    meeting.series_start_date = date(2026, 7, 1)
    meeting.series_end_date = date(2026, 8, 1)
    next_occurrence = MagicMock(occurrence_date=date(2026, 7, 29))

    with patch(
        "app.services.scheduled_meeting_occurrences.resolve_series_occurrences",
        return_value=([], "rule"),
    ), patch(
        "app.services.scheduled_meeting_occurrences.find_next_after",
        return_value=next_occurrence,
    ):
        result = resolve_next_meeting_date_for_series(
            meeting,
            current_occurrence_date=date(2026, 7, 22),
        )

    assert result == "2026-07-29T00:00:00"


@pytest.mark.asyncio
async def test_build_protocol_creation_fields_for_one_time_meeting() -> None:
    entry = MeetingRegistryEntry(
        memo_ref_key="memo-ref",
        manager_name="Manager",
        invitations_sent_at=datetime.now(timezone.utc),
    )
    topic = {"ref_key": "topic-1", "keys": {"room": "room-1"}}
    db = AsyncMock()

    with patch(
        "app.services.meeting_protocol_fields.resolve_room_key",
        return_value="room-from-memo",
    ), patch(
        "app.services.meeting_protocol_fields.resolve_manager_department_key",
        return_value="dept-1",
    ), patch(
        "app.services.meeting_protocol_fields.resolve_basis_for_protocol",
        return_value=("memo-ref", MEMO_BASIS_TYPE),
    ):
        fields = await build_protocol_creation_fields(entry, topic, db=db)

    assert fields["is_series"] is False
    assert fields["room_key"] == "room-from-memo"
    assert fields["department_key"] == "dept-1"
    assert fields["basis_key"] == "memo-ref"
    assert fields["next_meeting_date"] is None
