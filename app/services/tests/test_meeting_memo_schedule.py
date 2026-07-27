from __future__ import annotations

import base64
from datetime import date

from app.models.enums import ScheduledMeetingFrequency, ScheduledMeetingWeekday
from app.services.meeting_memo_recurrence import resolve_memo_recurrence
from app.services.meeting_memo_schedule import (
    job_schedule_to_recurrence_rules,
    parse_job_schedule_fields,
    parse_memo_schedule,
)
from app.services.scheduled_meeting_recurrence import default_series_end_date


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


def _structure_property_daily_until_july30() -> bytes:
    """Формат OData/XDTO как в реальной СЗ (Property name + Value)."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Structure xmlns="http://v8.1c.ru/8.1/data/core" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
	<Property name="ДатаНачала">
		<Value xsi:type="xs:dateTime">0001-01-01T00:00:00</Value>
	</Property>
	<Property name="ДатаКонца">
		<Value xsi:type="xs:dateTime">2026-07-30T00:00:00</Value>
	</Property>
	<Property name="ДеньВМесяце">
		<Value xsi:type="xs:decimal">0</Value>
	</Property>
	<Property name="ДеньНеделиВМесяце">
		<Value xsi:type="xs:decimal">0</Value>
	</Property>
	<Property name="ПериодЛет">
		<Value xsi:type="xs:decimal">0</Value>
	</Property>
	<Property name="ПериодМесяцев">
		<Value xsi:type="xs:decimal">0</Value>
	</Property>
	<Property name="ПериодНедель">
		<Value xsi:type="xs:decimal">0</Value>
	</Property>
	<Property name="ПериодПовтораДней">
		<Value xsi:type="xs:decimal">1</Value>
	</Property>
	<Property name="ПовторениеПоДнямНедели">
		<Value xsi:type="Array">
			<Value xsi:type="xs:decimal">1</Value>
			<Value xsi:type="xs:decimal">2</Value>
			<Value xsi:type="xs:decimal">3</Value>
			<Value xsi:type="xs:decimal">4</Value>
			<Value xsi:type="xs:decimal">5</Value>
			<Value xsi:type="xs:decimal">6</Value>
			<Value xsi:type="xs:decimal">7</Value>
		</Value>
	</Property>
</Structure>"""
    return xml.encode("utf-8")


def test_parse_job_schedule_fields_daily_series() -> None:
    fields = parse_job_schedule_fields(_daily_two_weeks_xdto())
    assert fields is not None
    assert fields.begin_date == date(2026, 7, 24)
    assert fields.end_date == date(2026, 8, 6)
    assert fields.days_repeat_period == 1
    assert fields.begin_time is not None
    assert fields.begin_time.hour == 13


def test_parse_job_schedule_fields_structure_property_format() -> None:
    fields = parse_job_schedule_fields(_structure_property_daily_until_july30())
    assert fields is not None
    assert fields.begin_date is None  # 0001-01-01
    assert fields.end_date == date(2026, 7, 30)
    assert fields.days_repeat_period == 1
    assert fields.week_days == [1, 2, 3, 4, 5, 6, 7]


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


def test_job_schedule_uses_document_date_when_begin_empty() -> None:
    fields = parse_job_schedule_fields(_structure_property_daily_until_july30())
    assert fields is not None
    header = {
        "Date": "2026-07-27T10:02:13",
        "ЖелаемаяДатаПроведенияСовещания": "2026-07-30T00:00:00",
        "ВремяНачалаСовещания": "0001-01-01T10:00:00",
        "ВремяОкончанияСовещания": "0001-01-01T11:00:00",
    }
    parsed = job_schedule_to_recurrence_rules(fields, header)
    assert parsed.frequency == ScheduledMeetingFrequency.DAILY
    assert parsed.series_start_date == date(2026, 7, 27)
    assert parsed.series_end_date == date(2026, 7, 30)


def test_job_schedule_default_end_is_dec31_current_year() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Structure xmlns="http://v8.1c.ru/8.1/data/core" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Property name="ДатаНачала"><Value xsi:type="xs:dateTime">2026-07-01T00:00:00</Value></Property>
  <Property name="ДатаКонца"><Value xsi:type="xs:dateTime">0001-01-01T00:00:00</Value></Property>
  <Property name="ПериодПовтораДней"><Value xsi:type="xs:decimal">1</Value></Property>
  <Property name="ПовторениеПоДнямНедели">
    <Value xsi:type="Array">
      <Value xsi:type="xs:decimal">1</Value>
      <Value xsi:type="xs:decimal">2</Value>
      <Value xsi:type="xs:decimal">3</Value>
      <Value xsi:type="xs:decimal">4</Value>
      <Value xsi:type="xs:decimal">5</Value>
    </Value>
  </Property>
</Structure>"""
    fields = parse_job_schedule_fields(xml.encode("utf-8"))
    assert fields is not None
    parsed = job_schedule_to_recurrence_rules(
        fields,
        {
            "ВремяНачалаСовещания": "0001-01-01T09:00:00",
            "ВремяОкончанияСовещания": "0001-01-01T10:00:00",
        },
    )
    assert parsed.series_end_date == default_series_end_date(year=date.today().year)


def test_job_schedule_single_weekday_maps_to_weekly() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Structure xmlns="http://v8.1c.ru/8.1/data/core" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Property name="ДатаНачала"><Value xsi:type="xs:dateTime">2026-07-01T00:00:00</Value></Property>
  <Property name="ДатаКонца"><Value xsi:type="xs:dateTime">2026-08-01T00:00:00</Value></Property>
  <Property name="ПериодПовтораДней"><Value xsi:type="xs:decimal">1</Value></Property>
  <Property name="ПовторениеПоДнямНедели">
    <Value xsi:type="Array"><Value xsi:type="xs:decimal">3</Value></Value>
  </Property>
</Structure>"""
    fields = parse_job_schedule_fields(xml.encode("utf-8"))
    assert fields is not None
    parsed = job_schedule_to_recurrence_rules(
        fields,
        {
            "ВремяНачалаСовещания": "0001-01-01T09:00:00",
            "ВремяОкончанияСовещания": "0001-01-01T10:00:00",
        },
    )
    assert parsed.frequency == ScheduledMeetingFrequency.WEEKLY
    assert parsed.weekday == ScheduledMeetingWeekday.WEDNESDAY


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
    assert draft.occurrence_count == 10
    assert draft.source_quote is not None
    assert "Расписание 1С" in draft.source_quote


def test_resolve_memo_recurrence_structure_schedule_like_11991() -> None:
    """Без даты начала в JobSchedule — старт с даты документа, не с желаемой."""
    header = {
        "Date": "2026-07-27T10:02:13",
        "ЖелаемаяДатаПроведенияСовещания": "2026-07-30T00:00:00",
        "ВремяНачалаСовещания": "0001-01-01T10:00:00",
        "ВремяОкончанияСовещания": "0001-01-01T11:00:00",
        "Расписание_Base64Data": base64.b64encode(
            _structure_property_daily_until_july30()
        ).decode("ascii"),
        "ТекстСлужебнойЗаписки": "",
        "ТемаСовещания": "ДПИ ИИ",
    }
    draft = resolve_memo_recurrence(header)
    assert draft.is_series is True
    assert draft.recurrence is not None
    assert draft.recurrence.frequency == ScheduledMeetingFrequency.DAILY
    assert draft.recurrence.series_start_date == date(2026, 7, 27)
    assert draft.recurrence.series_end_date == date(2026, 7, 30)
    # 27–30.07.2026 — пн–чт, ежедневно по будням → 4 встречи
    assert draft.occurrence_count == 4
    assert "series" in draft.planning_options


def test_parse_memo_schedule_returns_none_without_payload() -> None:
    assert parse_memo_schedule({"Расписание_Type": "application/xml+xdto"}) is None


def test_refresh_series_planning_from_cached_queue_keeps_schedule_detection() -> None:
    """После обогащения detail расписание должно читаться из queue (кэш)."""
    from app.services.meeting_memo_cache import refresh_series_planning

    schedule_b64 = base64.b64encode(_structure_property_daily_until_july30()).decode("ascii")
    detail = {
        "ref_key": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "number": "000011991",
        "queue": {
            "ref_key": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "ЖелаемаяДатаПроведенияСовещания": "2026-07-28T00:00:00",
            "ВремяНачалаСовещания": "0001-01-01T12:00:00",
            "ВремяОкончанияСовещания": "0001-01-01T12:20:00",
            "Расписание_Base64Data": schedule_b64,
            "ТекстСлужебнойЗаписки": "",
        },
        "application": {
            "meeting_start": "2026-07-28T12:00:00",
            "meeting_end": "2026-07-28T12:20:00",
        },
        "series_planning": {"detected": False, "planning_options": ["single"]},
    }

    planning = refresh_series_planning(detail)

    assert planning["detected"] is True
    assert "series" in planning["planning_options"]
    assert detail["queue"]["series_detected"] is True
