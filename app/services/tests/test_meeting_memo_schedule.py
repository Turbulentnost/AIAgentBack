from __future__ import annotations

import base64
from datetime import date

from app.models.enums import ScheduledMeetingFrequency
from app.services.meeting_memo_recurrence import resolve_memo_recurrence
from app.services.meeting_memo_schedule import (
    job_schedule_to_recurrence_rules,
    parse_job_schedule_fields,
    parse_memo_schedule,
)


def _daily_two_weeks_xdto() -> bytes:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<JobSchedule xmlns="http://v8.1c.ru/8.1/data/enterprise">
  <BeginDate>2026-07-24T00:00:00</BeginDate>
  <EndDate>2026-08-06T00:00:00</EndDate>
  <BeginTime>0001-01-01T13:00:00</BeginTime>
  <EndTime>0001-01-01T13:20:00</EndTime>
  <DaysRepeatPeriod>1</DaysRepeatPeriod>
  <WeeksPeriod>0</WeeksPeriod>
  <RepeatPeriodInDay>0</RepeatPeriodInDay>
</JobSchedule>"""
    return xml.encode("utf-8")


def test_parse_job_schedule_fields_daily_series() -> None:
    fields = parse_job_schedule_fields(_daily_two_weeks_xdto())
    assert fields is not None
    assert fields.begin_date == date(2026, 7, 24)
    assert fields.end_date == date(2026, 8, 6)
    assert fields.days_repeat_period == 1
    assert fields.begin_time is not None
    assert fields.begin_time.hour == 13


def test_job_schedule_to_recurrence_rules_daily_two_weeks() -> None:
    fields = parse_job_schedule_fields(_daily_two_weeks_xdto())
    assert fields is not None
    header = {
        "ЖелаемаяДатаПроведенияСовещания": "2026-07-24T00:00:00",
        "ВремяНачалаСовещания": "0001-01-01T13:00:00",
        "ВремяОкончанияСовещания": "0001-01-01T13:20:00",
    }
    parsed = job_schedule_to_recurrence_rules(fields, header)

    assert parsed.frequency == ScheduledMeetingFrequency.DAILY
    assert parsed.interval == 1
    assert parsed.series_start_date == date(2026, 7, 24)
    assert parsed.series_end_date == date(2026, 8, 6)
    assert parsed.duration_minutes == 20


def test_resolve_memo_recurrence_uses_schedule_before_text() -> None:
    header = {
        "ЖелаемаяДатаПроведенияСовещания": "2026-07-24T00:00:00",
        "ВремяНачалаСовещания": "0001-01-01T13:00:00",
        "ВремяОкончанияСовещания": "0001-01-01T13:20:00",
        "Расписание_Base64Data": base64.b64encode(_daily_two_weeks_xdto()).decode("ascii"),
        "ТекстСлужебнойЗаписки": "Прошу организовать совещание",
    }
    draft = resolve_memo_recurrence(header)

    assert draft.is_series is True
    assert draft.confidence == "high"
    assert draft.recurrence is not None
    assert draft.recurrence.frequency == ScheduledMeetingFrequency.DAILY
    assert draft.occurrence_count == 14
    assert draft.source_quote is not None
    assert "Расписание 1С" in draft.source_quote


def test_parse_memo_schedule_returns_none_without_payload() -> None:
    assert parse_memo_schedule({"Расписание_Type": "application/xml+xdto"}) is None
