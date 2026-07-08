"""Парсинг OData-полей и расписания из документов служебных записок."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

EMPTY_DATE_PREFIX = "0001-01-01"
_EXCEL_DATETIME_RE = re.compile(
    r"^(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$"
)
GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _parse_flexible_datetime(value: str) -> datetime | None:
    normalized = value.strip()
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        pass
    match = _EXCEL_DATETIME_RE.match(normalized)
    if not match:
        return None
    day, month, year, hour, minute, second = match.groups()
    return datetime(
        int(year),
        int(month),
        int(day),
        int(hour or 0),
        int(minute or 0),
        int(second or 0),
    )


def _is_time_only_sentinel(dt: datetime) -> bool:
    return dt.year == 1 and dt.month == 1 and dt.day == 1


def is_empty_odata_date(value: str | None) -> bool:
    normalized = (value or "").strip()
    if not normalized:
        return True
    parsed = _parse_flexible_datetime(normalized)
    if parsed is not None and _is_time_only_sentinel(parsed):
        return parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0
    if normalized.startswith(EMPTY_DATE_PREFIX):
        return True
    return False


def looks_like_guid(value: str) -> bool:
    return bool(GUID_PATTERN.match(value.strip()))


def clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def parse_odata_datetime(value: str | None) -> datetime | None:
    if is_empty_odata_date(value):
        return None
    return _parse_flexible_datetime(value.strip())


def parse_odata_date(value: str | None) -> date | None:
    parsed = parse_odata_datetime(value)
    return parsed.date() if parsed is not None else None


def parse_odata_time_component(value: str | None) -> tuple[int, int] | None:
    if not value or not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or is_empty_odata_date(normalized):
        return None
    dt = _parse_flexible_datetime(normalized)
    if dt is None:
        return None
    if _is_time_only_sentinel(dt):
        if dt.hour == 0 and dt.minute == 0:
            return None
        return dt.hour, dt.minute
    return dt.hour, dt.minute


def resolve_meeting_schedule(header: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    meeting_day = (
        parse_odata_datetime(header.get("ДатаПроведенияСовещания"))
        or parse_odata_datetime(header.get("ЖелаемаяДатаПроведенияСовещания"))
    )

    start_raw = header.get("ВремяНачалаСовещания")
    end_raw = header.get("ВремяОкончанияСовещания")
    if start_raw and not is_empty_odata_date(start_raw):
        start = parse_odata_datetime(start_raw)
        end = parse_odata_datetime(end_raw) if end_raw and not is_empty_odata_date(end_raw) else None
        if start is not None and not _is_time_only_sentinel(start):
            return start, end

    start_time = parse_odata_time_component(start_raw)
    end_time = parse_odata_time_component(end_raw)
    if meeting_day and start_time:
        start = meeting_day.replace(hour=start_time[0], minute=start_time[1], second=0, microsecond=0)
        end = None
        if end_time:
            end = meeting_day.replace(hour=end_time[0], minute=end_time[1], second=0, microsecond=0)
        return start, end
    return None, None


def schedule_duration_minutes(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    minutes = int((end - start).total_seconds() // 60)
    return minutes if minutes > 0 else None
