from __future__ import annotations

import uuid
from datetime import date, time
from types import SimpleNamespace

import pytest

from app.models.enums import (
    ScheduledMeetingFrequency,
    ScheduledMeetingStatus,
    ScheduledMeetingType,
    ScheduledMeetingWeekday,
)
from app.schemas.scheduled_meeting import (
    ScheduledMeetingParticipantCreate,
    ScheduledMeetingRecurrencePayload,
    ScheduledMeetingUpdate,
)
from app.services.scheduled_meeting_diff import build_series_update_change_set


def _meeting_stub() -> SimpleNamespace:
    position_id = uuid.uuid4()
    position = SimpleNamespace(id=position_id, name="Директор", is_active=True)
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="Тест",
        meeting_type=ScheduledMeetingType.PLANNED,
        status=ScheduledMeetingStatus.PLANNED,
        time_local=time(9, 0),
        duration_minutes=60,
        frequency=ScheduledMeetingFrequency.DAILY,
        interval=1,
        monthly_mode=None,
        day_of_month=None,
        weekday=None,
        weekday_position=None,
        series_start_date=date(2026, 7, 15),
        series_end_date=date(2026, 7, 17),
        participants=[
            SimpleNamespace(
                position_id=position_id,
                sort_order=0,
            )
        ],
        payload={"comment": "старый"},
    )


def test_change_set_detects_series_end_extension() -> None:
    meeting = _meeting_stub()
    payload = ScheduledMeetingUpdate(series_end_date=date(2026, 7, 20))

    change_set = build_series_update_change_set(meeting, payload)

    assert change_set.series_end_changed is True
    assert change_set.new_series_end_date == date(2026, 7, 20)
    assert change_set.unsupported_fields == ()


def test_change_set_detects_series_end_shortening() -> None:
    meeting = _meeting_stub()
    payload = ScheduledMeetingUpdate(series_end_date=date(2026, 7, 16))

    change_set = build_series_update_change_set(meeting, payload)

    assert change_set.series_end_changed is True
    assert change_set.new_series_end_date == date(2026, 7, 16)


def test_change_set_rejects_participants_change() -> None:
    meeting = _meeting_stub()
    other_position = uuid.uuid4()
    payload = ScheduledMeetingUpdate(
        series_end_date=date(2026, 7, 20),
        participants=[ScheduledMeetingParticipantCreate(position_id=other_position)],
    )

    change_set = build_series_update_change_set(meeting, payload)

    assert "участники" in change_set.unsupported_fields


def test_change_set_rejects_recurrence_schedule_change() -> None:
    meeting = _meeting_stub()
    payload = ScheduledMeetingUpdate(
        series_end_date=date(2026, 7, 20),
        recurrence=ScheduledMeetingRecurrencePayload(
            frequency=ScheduledMeetingFrequency.WEEKLY,
            interval=1,
            time_local=time(9, 0),
            duration_minutes=60,
            weekday=ScheduledMeetingWeekday.MONDAY,
        ),
    )

    change_set = build_series_update_change_set(meeting, payload)

    assert "периодичность" in change_set.unsupported_fields


def test_change_set_allows_comment_only_update() -> None:
    meeting = _meeting_stub()
    payload = ScheduledMeetingUpdate(comment="новый")

    change_set = build_series_update_change_set(meeting, payload)

    assert change_set.series_end_changed is False
    assert change_set.comment_changed is True
    assert change_set.unsupported_fields == ()
