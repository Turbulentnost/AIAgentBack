from datetime import date, time

import pytest

from app.models.enums import (
    ScheduledMeetingFrequency,
    ScheduledMeetingMonthlyMode,
    ScheduledMeetingWeekday,
    ScheduledMeetingWeekdayPosition,
)
from app.services.scheduled_meeting_recurrence import (
    RecurrenceInput,
    build_recurrence_rule,
    default_series_end_date,
    format_recurrence_label,
    validate_recurrence_input,
)


def _recurrence(**kwargs) -> RecurrenceInput:
    defaults = {
        "frequency": ScheduledMeetingFrequency.WEEKLY,
        "interval": 1,
        "time_local": time(10, 0),
        "duration_minutes": 60,
        "series_start_date": date(2026, 1, 1),
        "series_end_date": date(2026, 12, 31),
        "weekday": ScheduledMeetingWeekday.TUESDAY,
    }
    defaults.update(kwargs)
    return RecurrenceInput(**defaults)


def test_default_series_end_date_uses_year_end() -> None:
    assert default_series_end_date(year=2026) == date(2026, 12, 31)


def test_format_recurrence_label_weekly() -> None:
    label = format_recurrence_label(_recurrence())
    assert label == "еженедельно, вторник 10:00"


def test_format_recurrence_label_biweekly() -> None:
    label = format_recurrence_label(
        _recurrence(
            interval=2,
            weekday=ScheduledMeetingWeekday.WEDNESDAY,
            time_local=time(15, 0),
        )
    )
    assert label == "раз в две недели, среда 15:00"


def test_format_recurrence_label_monthly_first_tuesday() -> None:
    label = format_recurrence_label(
        _recurrence(
            frequency=ScheduledMeetingFrequency.MONTHLY,
            interval=1,
            monthly_mode=ScheduledMeetingMonthlyMode.BY_WEEKDAY_POSITION,
            weekday=ScheduledMeetingWeekday.TUESDAY,
            weekday_position=ScheduledMeetingWeekdayPosition.FIRST,
            time_local=time(10, 0),
        )
    )
    assert label == "ежемесячно, первый вторник 10:00"


def test_format_recurrence_label_monthly_last_friday() -> None:
    label = format_recurrence_label(
        _recurrence(
            frequency=ScheduledMeetingFrequency.MONTHLY,
            interval=1,
            monthly_mode=ScheduledMeetingMonthlyMode.BY_WEEKDAY_POSITION,
            weekday=ScheduledMeetingWeekday.FRIDAY,
            weekday_position=ScheduledMeetingWeekdayPosition.LAST,
            time_local=time(11, 30),
        )
    )
    assert label == "ежемесячно, последняя пятница 11:30"


def test_format_recurrence_label_quarterly_by_weekday_position() -> None:
    label = format_recurrence_label(
        _recurrence(
            frequency=ScheduledMeetingFrequency.MONTHLY,
            interval=3,
            monthly_mode=ScheduledMeetingMonthlyMode.BY_WEEKDAY_POSITION,
            weekday=ScheduledMeetingWeekday.TUESDAY,
            weekday_position=ScheduledMeetingWeekdayPosition.FIRST,
            time_local=time(10, 0),
        )
    )
    assert label == "ежеквартально, первый вторник 10:00"


def test_format_recurrence_label_quarterly() -> None:
    label = format_recurrence_label(
        _recurrence(
            frequency=ScheduledMeetingFrequency.MONTHLY,
            interval=3,
            monthly_mode=ScheduledMeetingMonthlyMode.BY_DAY_OF_MONTH,
            day_of_month=15,
            weekday=None,
            time_local=time(10, 0),
        )
    )
    assert label == "ежеквартально, 15 число 10:00"


def test_build_recurrence_rule_contains_series_end_date() -> None:
    rule = build_recurrence_rule(_recurrence())
    assert rule["series_end_date"] == "2026-12-31"
    assert rule["weekday"] == "tuesday"


def test_validate_recurrence_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="series_end_date"):
        validate_recurrence_input(
            _recurrence(
                series_start_date=date(2026, 6, 1),
                series_end_date=date(2026, 5, 1),
            )
        )


def test_validate_recurrence_requires_weekday_for_weekly() -> None:
    with pytest.raises(ValueError, match="weekday"):
        validate_recurrence_input(_recurrence(weekday=None))
