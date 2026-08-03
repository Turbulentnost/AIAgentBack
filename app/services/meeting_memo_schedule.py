"""Парсинг поля «Расписание» (JobSchedule XDTO) из служебной записки 1С."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from datetime import date, time
from typing import Any, Literal
from xml.etree import ElementTree as ET

from app.models.enums import ScheduledMeetingFrequency, ScheduledMeetingWeekday
from app.services.meeting_memo_document import (
    is_empty_odata_date,
    parse_odata_date,
    parse_odata_time_component,
)
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

_WORKDAYS = frozenset({1, 2, 3, 4, 5})
_ALL_WEEKDAYS = frozenset({1, 2, 3, 4, 5, 6, 7})

FieldKind = Literal["date", "time", "int", "weekdays", "months"]

# Имена свойств JobSchedule: и плоский XML, и XDTO Property/@name.
_FIELD_BY_NAME: dict[str, tuple[str, FieldKind]] = {
    "BeginDate": ("begin_date", "date"),
    "ДатаНачала": ("begin_date", "date"),
    "EndDate": ("end_date", "date"),
    "CompletionDate": ("end_date", "date"),
    "ДатаКонца": ("end_date", "date"),
    "ДатаОкончания": ("end_date", "date"),
    "BeginTime": ("begin_time", "time"),
    "ВремяНачала": ("begin_time", "time"),
    "EndTime": ("end_time", "time"),
    "CompletionTime": ("end_time", "time"),
    "ВремяКонца": ("end_time", "time"),
    "ВремяОкончания": ("end_time", "time"),
    "DaysRepeatPeriod": ("days_repeat_period", "int"),
    "ПериодПовтораДней": ("days_repeat_period", "int"),
    "WeeksPeriod": ("weeks_period", "int"),
    "ПериодПовтораНедель": ("weeks_period", "int"),
    "ПериодНедель": ("weeks_period", "int"),
    "MonthsPeriod": ("months_period", "int"),
    "ПериодПовтораМесяцев": ("months_period", "int"),
    "ПериодМесяцев": ("months_period", "int"),
    "YearsPeriod": ("years_period", "int"),
    "ПериодПовтораЛет": ("years_period", "int"),
    "ПериодЛет": ("years_period", "int"),
    "DayInMonth": ("day_in_month", "int"),
    "ДеньВМесяце": ("day_in_month", "int"),
    "WeekDayInMonth": ("week_day_in_month", "int"),
    "ДеньНеделиВМесяце": ("week_day_in_month", "int"),
    "RepeatPeriodInDay": ("repeat_period_in_day", "int"),
    "ПериодПовтораВТечениеДня": ("repeat_period_in_day", "int"),
    "WeekDays": ("week_days", "weekdays"),
    "WeekDay": ("week_days", "weekdays"),
    "ДниНедели": ("week_days", "weekdays"),
    "ДеньНедели": ("week_days", "weekdays"),
    "ПовторениеПоДнямНедели": ("week_days", "weekdays"),
    "Months": ("months", "months"),
    "Month": ("months", "months"),
    "Месяц": ("months", "months"),
    "ПовторениеПоМесяцам": ("months", "months"),
}


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
        return int(float(normalized))
    except ValueError:
        return 0


def _parse_date_value(value: str | None) -> date | None:
    if not value or not value.strip():
        return None
    if is_empty_odata_date(value.strip()):
        return None
    parsed = parse_odata_date(value.strip())
    if parsed is None or parsed.year <= 1:
        return None
    return parsed


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


def _iter_value_texts(element: ET.Element) -> list[str]:
    """Собирает текстовые Value из Property/Array (включая вложенные)."""
    texts: list[str] = []
    tag = _local_name(element.tag)
    if tag == "Value":
        if element.text and element.text.strip():
            texts.append(element.text.strip())
        for child in element:
            texts.extend(_iter_value_texts(child))
        return texts
    for child in element:
        texts.extend(_iter_value_texts(child))
    return texts


def _apply_field(fields: JobScheduleFields, attr: str, kind: FieldKind, raw_values: list[str]) -> None:
    if kind == "date":
        for raw in raw_values:
            parsed = _parse_date_value(raw)
            if parsed is not None:
                setattr(fields, attr, parsed)
                return
        return
    if kind == "time":
        for raw in raw_values:
            parsed = _parse_time_value(raw)
            if parsed is not None:
                setattr(fields, attr, parsed)
                return
        return
    if kind == "int":
        for raw in raw_values:
            setattr(fields, attr, _parse_int(raw))
            return
        return
    if kind == "weekdays":
        days = list(fields.week_days)
        for raw in raw_values:
            for day in _collect_weekday_values(raw):
                if day not in days:
                    days.append(day)
        fields.week_days = days
        return
    if kind == "months":
        months = list(fields.months)
        for raw in raw_values:
            month = _parse_int(raw)
            if 1 <= month <= 12 and month not in months:
                months.append(month)
        fields.months = months


def _parse_property_structure(root: ET.Element, fields: JobScheduleFields) -> bool:
    """1С XDTO: <Property name="..."><Value>...</Value></Property>."""
    found = False
    for element in root.iter():
        if _local_name(element.tag) != "Property":
            continue
        name = (element.attrib.get("name") or "").strip()
        mapping = _FIELD_BY_NAME.get(name)
        if mapping is None:
            continue
        attr, kind = mapping
        values = _iter_value_texts(element)
        if not values:
            continue
        _apply_field(fields, attr, kind, values)
        found = True
    return found


def _parse_flat_elements(root: ET.Element, fields: JobScheduleFields) -> None:
    """Плоский JobSchedule XML (тесты / старый формат)."""
    for element in root.iter():
        name = _local_name(element.tag)
        mapping = _FIELD_BY_NAME.get(name)
        if mapping is None:
            continue
        attr, kind = mapping
        values: list[str] = []
        if element.text and element.text.strip():
            values.append(element.text.strip())
        for child in element:
            child_tag = _local_name(child.tag)
            if child_tag in {"Day", "День", "Value", "Month", "Месяц"} and child.text:
                values.append(child.text.strip())
        if values:
            _apply_field(fields, attr, kind, values)


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
    if not _parse_property_structure(root, fields):
        _parse_flat_elements(root, fields)

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
    if fields.months_period > 0 or fields.years_period > 0:
        return True
    if fields.day_in_month != 0 or fields.week_day_in_month != 0:
        return True
    if fields.weeks_period > 0:
        return True
    if fields.days_repeat_period > 1:
        return True
    if fields.days_repeat_period == 1:
        if fields.week_days:
            return True
        if fields.end_date is not None:
            if fields.begin_date is None or fields.end_date > fields.begin_date:
                return True
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
    if fields.weeks_period > 0:
        parts.append(f"раз в {max(1, fields.weeks_period)} нед.")
    if fields.week_days:
        labels = [
            _ONEC_WEEKDAY_TO_MODEL[day].value
            for day in fields.week_days
            if day in _ONEC_WEEKDAY_TO_MODEL
        ]
        if labels and set(fields.week_days) not in {_WORKDAYS, _ALL_WEEKDAYS}:
            parts.append("по " + ", ".join(labels))
    if fields.begin_time:
        parts.append(f"в {fields.begin_time.strftime('%H:%M')}")
    return "Расписание 1С: " + ", ".join(parts) if parts else "Расписание 1С"


def _series_start_from_header(header: dict[str, Any]) -> date | None:
    """Дата начала серии, если в JobSchedule она не задана.

    Сначала дата документа СЗ (иначе «ежедневно до X» с желаемой датой = X
    схлопывается в одну встречу). Если даты документа нет — сегодня.
    """
    for key in ("Date", "Дата", "document_date"):
        parsed = parse_odata_date(header.get(key))
        if parsed is not None and parsed.year > 1:
            return parsed
    return date.today()


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

    # Дата начала неизвестна → дата документа СЗ (см. _series_start_from_header).
    series_start = fields.begin_date or _series_start_from_header(header)
    if series_start is None:
        ambiguities.append("Не указана дата начала серии в расписании")

    # Дата конца не указана → 31.12 текущего года.
    series_end = fields.end_date or default_series_end_date(year=date.today().year)

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
    week_set = set(fields.week_days)

    if fields.weeks_period > 0:
        if len(fields.week_days) > 1:
            ambiguities.append("Расписание 1С с несколькими днями недели пока не поддерживается")
            return parsed
        parsed.frequency = ScheduledMeetingFrequency.WEEKLY
        parsed.interval = max(1, fields.weeks_period)
        if fields.week_days:
            weekday = _ONEC_WEEKDAY_TO_MODEL.get(fields.week_days[0])
    elif fields.days_repeat_period >= 1:
        if not week_set or week_set == _WORKDAYS or week_set == _ALL_WEEKDAYS:
            parsed.frequency = ScheduledMeetingFrequency.DAILY
            parsed.interval = max(1, fields.days_repeat_period)
        elif len(fields.week_days) == 1:
            parsed.frequency = ScheduledMeetingFrequency.WEEKLY
            parsed.interval = 1
            weekday = _ONEC_WEEKDAY_TO_MODEL.get(fields.week_days[0])
        else:
            ambiguities.append("Расписание 1С с несколькими днями недели пока не поддерживается")
            return parsed
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
