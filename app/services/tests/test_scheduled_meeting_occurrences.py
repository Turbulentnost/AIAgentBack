from __future__ import annotations

from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.models.enums import (
    ScheduledMeetingFrequency,
    ScheduledMeetingMonthlyMode,
    ScheduledMeetingWeekday,
)
from app.services.scheduled_meeting_occurrences import (
    SeriesOccurrence,
    find_next_after,
    find_next_occurrence,
)
from app.services.scheduled_meeting_recurrence import RecurrenceInput, iter_occurrence_dates


def _recurrence(**kwargs) -> RecurrenceInput:
    defaults = {
        "frequency": ScheduledMeetingFrequency.DAILY,
        "interval": 1,
        "time_local": time(9, 0),
        "duration_minutes": 30,
        "series_start_date": date(2026, 7, 15),
        "series_end_date": date(2026, 7, 17),
    }
    defaults.update(kwargs)
    return RecurrenceInput(**defaults)


def test_iter_occurrence_dates_daily_series() -> None:
    dates = iter_occurrence_dates(_recurrence())
    assert dates == [date(2026, 7, 15), date(2026, 7, 16), date(2026, 7, 17)]


def test_iter_occurrence_dates_weekly_series() -> None:
    dates = iter_occurrence_dates(
        _recurrence(
            frequency=ScheduledMeetingFrequency.WEEKLY,
            interval=1,
            weekday=ScheduledMeetingWeekday.TUESDAY,
            series_start_date=date(2026, 7, 14),
            series_end_date=date(2026, 7, 28),
        )
    )
    assert dates == [date(2026, 7, 14), date(2026, 7, 21), date(2026, 7, 28)]


def test_iter_occurrence_dates_monthly_by_day() -> None:
    dates = iter_occurrence_dates(
        _recurrence(
            frequency=ScheduledMeetingFrequency.MONTHLY,
            interval=1,
            monthly_mode=ScheduledMeetingMonthlyMode.BY_DAY_OF_MONTH,
            day_of_month=15,
            series_start_date=date(2026, 1, 1),
            series_end_date=date(2026, 3, 31),
        ),
        range_start=date(2026, 2, 1),
        range_end=date(2026, 3, 31),
    )
    assert dates == [date(2026, 2, 15), date(2026, 3, 15)]


def test_find_next_occurrence_returns_first_future_slot() -> None:
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 7, 15, 10, 0, tzinfo=tz)
    occurrences = [
        SeriesOccurrence(
            occurrence_date=date(2026, 7, 15),
            slot_start=datetime(2026, 7, 15, 9, 0, tzinfo=tz),
            slot_end=datetime(2026, 7, 15, 9, 30, tzinfo=tz),
            outlook_item_id="a",
            outlook_changekey=None,
            subject="A",
            is_cancelled=False,
            source="rule",
        ),
        SeriesOccurrence(
            occurrence_date=date(2026, 7, 16),
            slot_start=datetime(2026, 7, 16, 9, 0, tzinfo=tz),
            slot_end=datetime(2026, 7, 16, 9, 30, tzinfo=tz),
            outlook_item_id="b",
            outlook_changekey=None,
            subject="B",
            is_cancelled=False,
            source="rule",
        ),
    ]

    assert find_next_occurrence(occurrences, now=now).occurrence_date == date(2026, 7, 16)


def test_find_next_after_returns_following_occurrence() -> None:
    tz = ZoneInfo("Europe/Moscow")
    occurrences = [
        SeriesOccurrence(
            occurrence_date=date(2026, 7, 15),
            slot_start=datetime(2026, 7, 15, 9, 0, tzinfo=tz),
            slot_end=datetime(2026, 7, 15, 9, 30, tzinfo=tz),
            outlook_item_id="a",
            outlook_changekey=None,
            subject="A",
            is_cancelled=False,
            source="rule",
        ),
        SeriesOccurrence(
            occurrence_date=date(2026, 7, 16),
            slot_start=datetime(2026, 7, 16, 9, 0, tzinfo=tz),
            slot_end=datetime(2026, 7, 16, 9, 30, tzinfo=tz),
            outlook_item_id="b",
            outlook_changekey=None,
            subject="B",
            is_cancelled=False,
            source="rule",
        ),
    ]

    assert find_next_after(occurrences, after_date=date(2026, 7, 15)).occurrence_date == date(2026, 7, 16)


def test_ews_datetime_to_aware_returns_std_datetime() -> None:
    from exchangelib import EWSDateTime, EWSTimeZone

    from app.services.scheduled_meeting_occurrences import _ews_datetime_to_aware

    ews_dt = EWSDateTime(2026, 7, 16, 9, 0, tzinfo=EWSTimeZone(key="Europe/Moscow"))
    result = _ews_datetime_to_aware(ews_dt, timezone_name="Europe/Moscow")
    assert type(result) is datetime
    assert result.year == 2026
    assert result.month == 7
    assert result.day == 16
    assert result.hour == 9


def test_calendar_item_to_occurrence_parses_series_occurrence() -> None:
    from app.services.scheduled_meeting_occurrences import _calendar_item_to_occurrence

    item = SimpleNamespace(
        type="Occurrence",
        is_cancelled=False,
        start=datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc),
        end=datetime(2026, 7, 15, 9, 30, tzinfo=timezone.utc),
        id="occ-1",
        changekey="ck-1",
        subject="Тест",
        recurring_master=SimpleNamespace(id="master-1"),
    )
    occurrence = _calendar_item_to_occurrence(item, timezone_name="Europe/Moscow")
    assert occurrence is not None
    assert occurrence.outlook_item_id == "occ-1"
    assert occurrence.occurrence_date == date(2026, 7, 15)


def test_calendar_item_belongs_to_series_refreshes_master_id() -> None:
    from app.services.scheduled_meeting_occurrences import _calendar_item_belongs_to_series

    stored_master_id = "AQMk-master"
    view_master_id = "AAMk-view"
    master = SimpleNamespace(id=view_master_id)

    def refresh() -> None:
        master.id = stored_master_id

    master.refresh = refresh
    item = SimpleNamespace(
        type="Occurrence",
        id="occ-1",
        recurring_master=lambda: master,
    )

    assert _calendar_item_belongs_to_series(
        item,
        stored_master_id,
        refreshed_master_ids={},
    )
    assert master.id == stored_master_id
