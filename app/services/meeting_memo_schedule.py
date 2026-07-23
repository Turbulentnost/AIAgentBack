"""Парсинг поля «Расписание» (JobSchedule XDTO) из служебной записки 1С."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from datetime import date, time
from typing import Any
from xml.etree import ElementTree as ET

from app.models.enums import ScheduledMeetingFrequency, ScheduledMeetingWeekday
from app.services.meeting_memo_document import parse_odata_date, parse_odata_time_component
from app.services.meeting_memo_recurrence import ParsedRecurrenceRules
from app.services.scheduled_meeting_recurrence import default_series_end_date

_LOCAL_TAG_RE = re.compile(r"^(\{.*\})?(.+)$")

_ONEC_WEEKDAY_TO_MODEL: dict[int, ScheduledMeetingWeekday] = {
    1: ScheduledMeetingWeekday.MONDAY,
    2: ScheduledMeetingWeekday.TUESDAY,
    3: ScheduledMeetingWeekday.WEDNESDAY,
    4: ScheduledMeetingWeekday.THURSDAY,
    5: ScheduledMeetingWeekday.FRIDAY,
    6: ScheduledMeetingWeekday.SATURDAY,
    7: ScheduledMeetingWeekday.SUNDAY,
}

_DATE_TAGS = frozenset(
    {
        "BeginDate",
        "EndDate",
        "CompletionDate",
        "ДатаНачала",
        "ДатаКонца",
        "ДатаОкончания",
    }
)
_TIME_TAGS = frozenset(
    {
        "BeginTime",
        "EndTime",
        "CompletionTime",
        "ВремяНачала",
        "ВремяКонца",
        "ВремяОкончания",
    }
)
_INT_TAGS = frozenset(
    {
        "DaysRepeatPeriod",
        "WeeksPeriod",
        "MonthsPeriod",
        "YearsPeriod",
        "DayInMonth",
        "WeekDayInMonth",
        "RepeatPeriodInDay",
        "ПериодПовтораДней",
        "ПериодПовтораНедель",
        "ПериодПовтораМесяцев",
        "ПериодПовтораЛет",
        "ДеньВМесяце",
        "ДеньНеделиВМесяце",
        "ПериодПовтораВТечениеДня",
    }
)
_WEEKDAY_TAGS = frozenset({"WeekDays", "WeekDay", "ДниНедели", "ДеньНедели"})


@dataclass(slots=True)
class JobScheduleFields:
    begin_date: date | None = None
    end_date: date | None = None
    begin_time: time | None = None
    end_time: time | None = None
    days_repeat_period: int = 0
    weeks_period: int = 0
    months_period: int = 0
    years_period: int = 0
    day_in_month: int = 0
    week_day_in_month: int = 0
    repeat_period_in_day: int = 0
    week_days: list[int] = field(default_factory=list)
    months: list[int] = field(default_factory=list)


def _local_name(tag: str) -> str:
    match = _LOCAL_TAG_RE.match(tag)
    return match.group(2) if match else tag


def _parse_int(value: str | None) -> int:
    if value is None:
        return 0
    normalized = value.strip()
    if not normalized:
        return 0
    try:
        return int(normalized)
    except ValueError:
        return 0


def _parse_date_value(value: str | None) -> date | None:
    if not value or not value.strip():
        return None
    return parse_odata_date(value.strip())


def _parse_time_value(value: str | None) -> time | None:
    if not value or not value.strip():
        return None
    components = parse_odata_time_component(value.strip())
    if components is None:
        return None
    return time(components[0], components[1])


def _collect_weekday_values(raw: str | None) -> list[int]:
    if not raw:
        return []
    values: list[int] = []
    for chunk in re.split(r"[,;\s]+", raw.strip()):
        if not chunk:
            continue
        day = _parse_int(chunk)
        if 1 <= day <= 7 and day not in values:
            values.append(day)
    return values


def extract_schedule_payload(header: dict[str, Any]) -> bytes | None:
    """Извлекает XDTO-полезную нагрузку расписания из шапки документа OData."""
    raw = header.get("Расписание_Base64Data")
    if isinstance(raw, str) and raw.strip():
        try:
            return base64.b64decode(raw.strip(), validate=False)
        except Exception:
            return raw.strip().encode("utf-8")
    if isinstance(raw, (bytes, bytearray)) and raw:
        return bytes(raw)
    return None


def parse_job_schedule_fields(payload: bytes | str) -> JobScheduleFields | None:
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="replace").strip()
    else:
        text = payload.strip()
    if not text:
        return None

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    fields = JobScheduleFields()
    for element in root.iter():
        tag = _local_name(element.tag)
        if tag in _DATE_TAGS:
            parsed = _parse_date_value(element.text)
            if parsed is None:
                continue
            if tag in {"BeginDate", "ДатаНачала"}:
                fields.begin_date = parsed
            elif tag in {"EndDate", "ДатаКонца", "ДатаОкончания", "CompletionDate"}:
                fields.end_date = parsed
            continue

        if tag in _TIME_TAGS:
            parsed = _parse_time_value(element.text)
            if parsed is None:
                continue
            if tag in {"BeginTime", "ВремяНачала"}:
                fields.begin_time = parsed
            elif tag in {"EndTime", "ВремяКонца", "ВремяОкончания"}:
                fields.end_time = parsed
            continue

        if tag in _INT_TAGS:
            value = _parse_int(element.text)
            if tag in {"DaysRepeatPeriod", "ПериодПовтораДней"}:
                fields.days_repeat_period = value
            elif tag in {"WeeksPeriod", "ПериодПовтораНедель"}:
                fields.weeks_period = value
            elif tag in {"MonthsPeriod", "ПериодПовтораМесяцев"}:
                fields.months_period = value
            elif tag in {"YearsPeriod", "ПериодПовтораЛет"}:
                fields.years_period = value
            elif tag in {"DayInMonth", "ДеньВМесяце"}:
                fields.day_in_month = value
            elif tag in {"WeekDayInMonth", "ДеньНеделиВМесяце"}:
                fields.week_day_in_month = value
            elif tag in {"RepeatPeriodInDay", "ПериодПовтораВТечениеДня"}:
                fields.repeat_period_in_day = value
            continue

        if tag in _WEEKDAY_TAGS:
            fields.week_days.extend(_collect_weekday_values(element.text))
            for child in element:
                child_tag = _local_name(child.tag)
                if child_tag in {"Day", "День", "Value"}:
                    day = _parse_int(child.text)
                    if 1 <= day <= 7 and day not in fields.week_days:
                        fields.week_days.append(day)
            continue

        if tag in {"Month", "Месяц"}:
            month = _parse_int(element.text)
            if 1 <= month <= 12 and month not in fields.months:
                fields.months.append(month)

    fields.week_days = sorted(set(fields.week_days))
    fields.months = sorted(set(fields.months))
    return fields


def _duration_minutes(begin: time | None, end: time | None) -> int | None:
    if begin is None or end is None:
        return None
    start_minutes = begin.hour * 60 + begin.minute
    end_minutes = end.hour * 60 + end.minute
    if end_minutes <= start_minutes:
        return None
    return end_minutes - start_minutes


def _is_multi_occurrence_schedule(fields: JobScheduleFields) -> bool:
    if fields.begin_date and fields.end_date and fields.end_date > fields.begin_date:
        return True
    if fields.days_repeat_period > 1:
        return True
    if fields.weeks_period > 1:
        return True
    if fields.months_period > 0 or fields.years_period > 0:
        return True
    if fields.day_in_month != 0 or fields.week_day_in_month != 0:
        return True
    if fields.days_repeat_period == 1 and fields.week_days:
        return True
    if fields.days_repeat_period == 1 and fields.repeat_period_in_day == 0:
        if fields.begin_date and fields.end_date:
            return fields.end_date > fields.begin_date
    return False


def _format_schedule_source(fields: JobScheduleFields) -> str:
    parts: list[str] = []
    if fields.begin_date:
        parts.append(f"с {fields.begin_date.strftime('%d.%m.%Y')}")
    if fields.end_date:
        parts.append(f"до {fields.end_date.strftime('%d.%m.%Y')}")
    if fields.days_repeat_period == 1 and not fields.weeks_period:
        parts.append("ежедневно")
    elif fields.days_repeat_period > 1:
        parts.append(f"каждые {fields.days_repeat_period} дн.")
    if fields.weeks_period > 1:
        parts.append(f"раз в {fields.weeks_period} нед.")
    if fields.week_days:
        labels = [
            _ONEC_WEEKDAY_TO_MODEL[day].value
            for day in fields.week_days
            if day in _ONEC_WEEKDAY_TO_MODEL
        ]
        if labels:
            parts.append("по " + ", ".join(labels))
    if fields.begin_time:
        parts.append(f"в {fields.begin_time.strftime('%H:%M')}")
    return "Расписание 1С: " + ", ".join(parts) if parts else "Расписание 1С"


def job_schedule_to_recurrence_rules(
    fields: JobScheduleFields,
    header: dict[str, Any],
) -> ParsedRecurrenceRules:
    parsed = ParsedRecurrenceRules(source_quote=_format_schedule_source(fields))
    ambiguities = parsed.ambiguities

    if not _is_multi_occurrence_schedule(fields):
        ambiguities.append("Расписание 1С описывает единоразовое совещание")
        return parsed

    if fields.months_period or fields.years_period or fields.day_in_month or fields.week_day_in_month:
        ambiguities.append("Месячное и годовое расписание 1С пока не поддерживается")
        return parsed

    if fields.repeat_period_in_day > 0:
        ambiguities.append("Несколько запусков в один день из расписания 1С не поддерживается")
        return parsed

    series_start = fields.begin_date or parse_odata_date(
        header.get("ЖелаемаяДатаПроведенияСовещания")
    ) or parse_odata_date(header.get("ДатаПроведенияСовещания"))
    if series_start is None:
        ambiguities.append("Не указана дата начала серии в расписании")

    series_end = fields.end_date
    if series_end is None and series_start is not None:
        series_end = default_series_end_date(year=series_start.year)

    time_local = fields.begin_time
    if time_local is None:
        start_components = parse_odata_time_component(header.get("ВремяНачалаСовещания"))
        if start_components:
            time_local = time(start_components[0], start_components[1])

    duration_minutes = _duration_minutes(fields.begin_time, fields.end_time)
    if duration_minutes is None:
        start_components = parse_odata_time_component(header.get("ВремяНачалаСовещания"))
        end_components = parse_odata_time_component(header.get("ВремяОкончанияСовещания"))
        if start_components and end_components:
            start_minutes = start_components[0] * 60 + start_components[1]
            end_minutes = end_components[0] * 60 + end_components[1]
            if end_minutes > start_minutes:
                duration_minutes = end_minutes - start_minutes

    if time_local is None:
        ambiguities.append("Не указано время начала серии")
    if duration_minutes is None:
        ambiguities.append("Не указана длительность серии")

    weekday: ScheduledMeetingWeekday | None = None
    if fields.week_days:
        if len(fields.week_days) > 1:
            ambiguities.append("Расписание 1С с несколькими днями недели пока не поддерживается")
            return parsed
        weekday = _ONEC_WEEKDAY_TO_MODEL.get(fields.week_days[0])

    if fields.weeks_period > 1 or fields.week_days:
        parsed.frequency = ScheduledMeetingFrequency.WEEKLY
        parsed.interval = max(1, fields.weeks_period or 1)
    elif fields.days_repeat_period >= 1:
        parsed.frequency = ScheduledMeetingFrequency.DAILY
        parsed.interval = max(1, fields.days_repeat_period)
    else:
        ambiguities.append("Не удалось определить периодичность серии из расписания 1С")
        return parsed

    if parsed.frequency == ScheduledMeetingFrequency.WEEKLY and weekday is None and series_start:
        weekday = _ONEC_WEEKDAY_TO_MODEL.get(series_start.isoweekday())

    parsed.weekday = weekday
    parsed.time_local = time_local
    parsed.duration_minutes = duration_minutes
    parsed.series_start_date = series_start
    parsed.series_end_date = series_end

    if series_start is not None and series_end is not None and series_end < series_start:
        ambiguities.append("Дата окончания серии раньше даты начала")

    return parsed


def parse_memo_schedule(header: dict[str, Any]) -> ParsedRecurrenceRules | None:
    payload = extract_schedule_payload(header)
    if payload is None:
        return None
    fields = parse_job_schedule_fields(payload)
    if fields is None:
        return ParsedRecurrenceRules(
            ambiguities=["Не удалось разобрать расписание 1С"],
            source_quote="Расписание 1С",
        )
    return job_schedule_to_recurrence_rules(fields, header)
