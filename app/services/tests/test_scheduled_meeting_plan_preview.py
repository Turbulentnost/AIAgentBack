from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.models.enums import (
    ScheduledMeetingFrequency,
    ScheduledMeetingStatus,
    ScheduledMeetingType,
    ScheduledMeetingWeekday,
)
from app.schemas.scheduled_meeting import ScheduledMeetingPlanPreviewRequest
from app.services.scheduled_meeting_occurrences import SeriesOccurrence
from app.services.scheduled_meeting_plan_preview import (
    build_plan_preview,
    evaluate_occurrence_preview,
    week_latest_allowed,
    work_week_friday,
)
from app.services.scheduled_meeting_service import ScheduledMeetingService


TZ = ZoneInfo("Europe/Moscow")


def _config() -> SimpleNamespace:
    return SimpleNamespace(timezone="Europe/Moscow")


def _occurrence(
    day: date,
    *,
    hour: int = 9,
    duration_minutes: int = 60,
) -> SeriesOccurrence:
    start = datetime.combine(day, time(hour, 0), tzinfo=TZ)
    return SeriesOccurrence(
        occurrence_date=day,
        slot_start=start,
        slot_end=start + timedelta(minutes=duration_minutes),
        outlook_item_id=None,
        outlook_changekey=None,
        subject="Проектная серия",
        is_cancelled=False,
        source="rule",
    )


def _meeting(**kwargs) -> SimpleNamespace:
    defaults = {
        "id": uuid.uuid4(),
        "title": "Проектная серия",
        "status": ScheduledMeetingStatus.CREATED,
        "outlook_series_id": None,
        "time_local": time(9, 0),
        "duration_minutes": 60,
        "frequency": ScheduledMeetingFrequency.WEEKLY,
        "interval": 1,
        "monthly_mode": None,
        "day_of_month": None,
        "weekday": ScheduledMeetingWeekday.WEDNESDAY,
        "weekday_position": None,
        "series_start_date": date(2026, 7, 15),
        "series_end_date": date(2026, 8, 12),
        "meeting_type": ScheduledMeetingType.PLANNED,
        "participants": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_week_latest_allowed_is_friday_work_end() -> None:
    # 2026-07-15 is Wednesday
    latest = week_latest_allowed(date(2026, 7, 15), timezone_name="Europe/Moscow")
    assert latest.date() == work_week_friday(date(2026, 7, 15))
    assert latest.hour == 17
    assert latest.minute == 0


def test_evaluate_ok_when_free() -> None:
    occurrence = _occurrence(date(2026, 7, 15))
    result = evaluate_occurrence_preview(
        occurrence=occurrence,
        conflict_policy="soft_week",
        attendees=["a@turbo-don.ru"],
        busy_by_attendee={"a@turbo-don.ru": []},
        config=_config(),
    )
    assert result.status == "ok"
    assert result.suggested_start is None


def test_evaluate_strict_keeps_conflict() -> None:
    occurrence = _occurrence(date(2026, 7, 15))
    busy = [
        (
            datetime(2026, 7, 15, 9, 0, tzinfo=TZ),
            datetime(2026, 7, 15, 10, 0, tzinfo=TZ),
        )
    ]
    result = evaluate_occurrence_preview(
        occurrence=occurrence,
        conflict_policy="strict",
        attendees=["a@turbo-don.ru"],
        busy_by_attendee={"a@turbo-don.ru": busy},
        config=_config(),
    )
    assert result.status == "conflict"
    assert result.busy_attendees == ["a@turbo-don.ru"]
    assert result.suggested_start is None


def test_evaluate_skip_marks_skip() -> None:
    occurrence = _occurrence(date(2026, 7, 15))
    busy = [
        (
            datetime(2026, 7, 15, 9, 0, tzinfo=TZ),
            datetime(2026, 7, 15, 10, 0, tzinfo=TZ),
        )
    ]
    result = evaluate_occurrence_preview(
        occurrence=occurrence,
        conflict_policy="skip",
        attendees=["a@turbo-don.ru"],
        busy_by_attendee={"a@turbo-don.ru": busy},
        config=_config(),
    )
    assert result.status == "skip"
    assert result.suggested_start is None


@patch("app.services.scheduled_meeting_plan_preview.find_quorum_slots")
def test_evaluate_soft_week_shifts_to_thursday(mock_find_quorum) -> None:
    occurrence = _occurrence(date(2026, 7, 15))  # Wednesday
    busy = [
        (
            datetime(2026, 7, 15, 9, 0, tzinfo=TZ),
            datetime(2026, 7, 15, 10, 0, tzinfo=TZ),
        )
    ]
    mock_find_quorum.return_value = {
        "candidates": [
            {
                "slot_start": "2026-07-16T10:00:00+03:00",
                "slot_end": "2026-07-16T11:00:00+03:00",
                "coverage": {"ratio": 1.0, "required_ok": True},
                "busy_attendees": [],
            }
        ]
    }
    result = evaluate_occurrence_preview(
        occurrence=occurrence,
        conflict_policy="soft_week",
        attendees=["a@turbo-don.ru"],
        busy_by_attendee={"a@turbo-don.ru": busy},
        config=_config(),
    )
    assert result.status == "shifted"
    assert result.suggested_start == "2026-07-16 10:00"
    assert result.suggested_end == "2026-07-16 11:00"
    kwargs = mock_find_quorum.call_args.kwargs
    assert kwargs["prefetched_busy_by_attendee"] == {"a@turbo-don.ru": busy}
    assert kwargs["min_coverage_ratio"] == 1.0
    assert kwargs["verify_calendar"] is False


@patch("app.services.scheduled_meeting_plan_preview.find_quorum_slots")
def test_evaluate_soft_week_unresolved_when_week_busy(mock_find_quorum) -> None:
    occurrence = _occurrence(date(2026, 7, 15))
    busy = [
        (
            datetime(2026, 7, 15, 9, 0, tzinfo=TZ),
            datetime(2026, 7, 15, 10, 0, tzinfo=TZ),
        )
    ]
    mock_find_quorum.return_value = {"candidates": []}
    result = evaluate_occurrence_preview(
        occurrence=occurrence,
        conflict_policy="soft_week",
        attendees=["a@turbo-don.ru"],
        busy_by_attendee={"a@turbo-don.ru": busy},
        config=_config(),
    )
    assert result.status == "unresolved"
    assert result.suggested_start is None


@pytest.mark.asyncio
async def test_build_plan_preview_fetches_freebusy_once() -> None:
    meeting = _meeting()
    occ1 = _occurrence(date(2026, 7, 15))
    occ2 = _occurrence(date(2026, 7, 22))
    db = AsyncMock()

    with (
        patch(
            "app.services.scheduled_meeting_plan_preview.resolve_attendee_emails",
            AsyncMock(return_value=["a@turbo-don.ru", "b@turbo-don.ru"]),
        ),
        patch(
            "app.services.scheduled_meeting_plan_preview.build_occurrences_from_rule",
            return_value=[occ1, occ2],
        ),
        patch(
            "app.services.scheduled_meeting_plan_preview.load_config",
            return_value=_config(),
        ),
        patch(
            "app.services.scheduled_meeting_plan_preview.fetch_busy_intervals_freebusy",
            return_value={"a@turbo-don.ru": [], "b@turbo-don.ru": []},
        ) as mock_freebusy,
        patch(
            "app.services.scheduled_meeting_plan_preview.asyncio.to_thread",
            new=AsyncMock(side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs)),
        ),
    ):
        result = await build_plan_preview(db, meeting, conflict_policy="soft_week")

    assert mock_freebusy.call_count == 1
    assert result.summary["total"] == 2
    assert result.summary["ok"] == 2
    assert all(item.status == "ok" for item in result.occurrences)


@pytest.mark.asyncio
async def test_service_plan_preview_rejects_non_created() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    meeting = _meeting(id=meeting_id, status=ScheduledMeetingStatus.PLANNED)
    loaded = MagicMock()
    loaded.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=loaded)

    with pytest.raises(Exception) as exc_info:
        await ScheduledMeetingService(db).plan_preview(
            meeting_id,
            ScheduledMeetingPlanPreviewRequest(conflict_policy="soft_week"),
        )
    assert getattr(exc_info.value, "status_code", None) == 409
