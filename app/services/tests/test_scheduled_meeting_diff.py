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
    user_id = uuid.uuid4()
    category_id = uuid.uuid4()
    position_id = uuid.uuid4()
    position = SimpleNamespace(id=position_id, name="Директор", is_active=True)
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="Тест",
        meeting_category_id=category_id,
        manager_user_id=user_id,
        responsible_user_id=user_id,
        manager_position_id=position_id,
        responsible_position_id=position_id,
        outlook_series_id="series-id",
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
                user_id=user_id,
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


def test_change_set_detects_participants_add() -> None:
    meeting = _meeting_stub()
    other_user_id = uuid.uuid4()
    current_user_id = meeting.participants[0].user_id
    payload = ScheduledMeetingUpdate(
        participants=[
            ScheduledMeetingParticipantCreate(
                user_id=current_user_id,
                person_fio="Current",
                person_email="current@turbo-don.ru",
                sort_order=0,
            ),
            ScheduledMeetingParticipantCreate(
                user_id=other_user_id,
                person_fio="Other",
                person_email="other@turbo-don.ru",
                sort_order=1,
            ),
        ],
    )

    change_set = build_series_update_change_set(meeting, payload)

    assert change_set.participants_changed is True
    assert change_set.participants_added == (other_user_id,)
    assert change_set.participants_removed == ()
    assert "участники" not in change_set.unsupported_fields
    assert "удаление участников" not in change_set.unsupported_fields


def test_change_set_detects_participants_remove() -> None:
    meeting = _meeting_stub()
    removed_user_id = uuid.uuid4()
    current_user_id = meeting.participants[0].user_id
    meeting.participants.append(
        SimpleNamespace(user_id=removed_user_id, sort_order=1),
    )
    payload = ScheduledMeetingUpdate(
        participants=[
            ScheduledMeetingParticipantCreate(
                user_id=current_user_id,
                person_fio="Current",
                person_email="current@turbo-don.ru",
            )
        ],
    )

    change_set = build_series_update_change_set(meeting, payload)

    assert change_set.participants_changed is True
    assert change_set.participants_removed == (removed_user_id,)
    assert change_set.participants_added == ()
    assert "удаление участников" not in change_set.unsupported_fields


def test_change_set_detects_recurrence_schedule_change() -> None:
    meeting = _meeting_stub()
    payload = ScheduledMeetingUpdate(
        recurrence=ScheduledMeetingRecurrencePayload(
            frequency=ScheduledMeetingFrequency.WEEKLY,
            interval=1,
            time_local=time(9, 0),
            duration_minutes=60,
            weekday=ScheduledMeetingWeekday.MONDAY,
        ),
    )

    change_set = build_series_update_change_set(meeting, payload)

    assert change_set.recurrence_changed is True
    assert change_set.new_recurrence is not None
    assert change_set.unsupported_fields == ()


def test_change_set_rejects_role_change_after_planning() -> None:
    meeting = _meeting_stub()
    payload = ScheduledMeetingUpdate(meeting_category_id=uuid.uuid4())

    change_set = build_series_update_change_set(meeting, payload)

    assert "вид совещания" in change_set.unsupported_fields


def test_change_set_allows_comment_only_update() -> None:
    meeting = _meeting_stub()
    payload = ScheduledMeetingUpdate(comment="новый")

    change_set = build_series_update_change_set(meeting, payload)

    assert change_set.series_end_changed is False
    assert change_set.comment_changed is True
    assert change_set.unsupported_fields == ()
