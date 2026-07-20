"""Фильтры списка писем (даты в часовом поясе Europe/Moscow)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


def parse_optional_date(value: str | None) -> date | None:
    if not value or not value.strip():
        return None
    return date.fromisoformat(value.strip())


def msk_day_start_utc(day: date) -> datetime:
    start = datetime.combine(day, time.min, tzinfo=MSK)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def msk_day_end_exclusive_utc(day: date) -> datetime:
    return msk_day_start_utc(day + timedelta(days=1))
