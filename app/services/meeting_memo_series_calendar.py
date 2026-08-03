"""Календарный контекст для LLM при расчёте срока серии совещаний."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

_WEEKDAY_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


def _month_last_day(value: date) -> date:
    last_day = calendar.monthrange(value.year, value.month)[1]
    return date(value.year, value.month, last_day)


def _quarter_last_day(value: date) -> date:
    quarter_end_month = ((value.month - 1) // 3 + 1) * 3
    last_day = calendar.monthrange(value.year, quarter_end_month)[1]
    return date(value.year, quarter_end_month, last_day)


def format_series_calendar_context(anchor: date) -> str:
    """Подсказки для LLM: границы недели, месяца, квартала от якорной даты."""
    monday = anchor - timedelta(days=anchor.weekday())
    friday = monday + timedelta(days=4)
    month_end = _month_last_day(anchor)
    quarter_end = _quarter_last_day(anchor)
    quarter = (anchor.month - 1) // 3 + 1
    return "\n".join(
        [
            "Календарный контекст для расчёта срока серии:",
            f"- якорная дата: {anchor.isoformat()} ({_WEEKDAY_RU[anchor.weekday()]})",
            f"- рабочая неделя (пн–пт): {monday.isoformat()} — {friday.isoformat()}",
            f"- конец месяца: {month_end.isoformat()}",
            f"- конец квартала Q{quarter}: {quarter_end.isoformat()}",
            f"- конец года: {anchor.year}-12-31",
        ]
    )
