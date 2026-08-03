from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.models.enums import (
    ScheduledMeetingFrequency,
    ScheduledMeetingMonthlyMode,
    ScheduledMeetingWeekday,
    ScheduledMeetingWeekdayPosition,
)

WEEKDAY_TO_ISO: dict[ScheduledMeetingWeekday, int] = {
    ScheduledMeetingWeekday.MONDAY: 0,
    ScheduledMeetingWeekday.TUESDAY: 1,
    ScheduledMeetingWeekday.WEDNESDAY: 2,
    ScheduledMeetingWeekday.THURSDAY: 3,
    ScheduledMeetingWeekday.FRIDAY: 4,
    ScheduledMeetingWeekday.SATURDAY: 5,
    ScheduledMeetingWeekday.SUNDAY: 6,
}

WEEKDAY_LABELS_RU: dict[ScheduledMeetingWeekday, str] = {
    ScheduledMeetingWeekday.MONDAY: "понедельник",
    ScheduledMeetingWeekday.TUESDAY: "вторник",
    ScheduledMeetingWeekday.WEDNESDAY: "среда",
    ScheduledMeetingWeekday.THURSDAY: "четверг",
    ScheduledMeetingWeekday.FRIDAY: "пятница",
    ScheduledMeetingWeekday.SATURDAY: "суббота",
    ScheduledMeetingWeekday.SUNDAY: "воскресенье",
}

WEEKDAY_POSITION_LABELS_RU: dict[ScheduledMeetingWeekdayPosition, str] = {
    ScheduledMeetingWeekdayPosition.FIRST: "первый",
    ScheduledMeetingWeekdayPosition.SECOND: "второй",
    ScheduledMeetingWeekdayPosition.THIRD: "третий",
    ScheduledMeetingWeekdayPosition.FOURTH: "четвёртый",
    ScheduledMeetingWeekdayPosition.LAST: "последняя",
}


def default_series_end_date(*, year: int | None = None) -> date:
    resolved_year = year if year is not None else date.today().year
    return date(resolved_year, 12, 31)


def format_time_label(value: time) -> str:
    text = value.strftime("%H:%M")
    if text.startswith("0"):
        return text[1:]
    return text


def is_weekend(value: date) -> bool:
    """Суббота и воскресенье (ISO: 5, 6)."""
    return value.weekday() >= 5


def first_weekday_on_or_after(value: date) -> date:
    current = value
    while is_weekend(current):
        current += timedelta(days=1)
    return current


def adjust_weekend_to_preceding_weekday(value: date) -> date:
    """Если дата — выходной, перенести на предыдущую пятницу.

    Суббота → пятница (−1), воскресенье → пятница (−2).
    Пример: последний день месяца 31.01 (сб) → 30.01 (пт).
    """
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value - timedelta(days=2)
    return value


def _business_day_index(anchor: date, value: date) -> int:
    """0-based индекс рабочего дня value относительно anchor (оба — будни)."""
    if value < anchor:
        return -1
    index = 0
    current = anchor
    while current < value:
        current += timedelta(days=1)
        if not is_weekend(current):
            index += 1
    return index


@dataclass(frozen=True)
class RecurrenceInput:
    frequency: ScheduledMeetingFrequency
    interval: int
    time_local: time
    duration_minutes: int
    series_start_date: date
    series_end_date: date
    monthly_mode: ScheduledMeetingMonthlyMode | None = None
    day_of_month: int | None = None
    weekday: ScheduledMeetingWeekday | None = None
    weekday_position: ScheduledMeetingWeekdayPosition | None = None


def validate_recurrence_input(data: RecurrenceInput) -> None:
    if data.interval < 1:
        raise ValueError("interval должен быть >= 1")
    if data.series_end_date < data.series_start_date:
        raise ValueError("series_end_date не может быть раньше series_start_date")

    if data.frequency == ScheduledMeetingFrequency.WEEKLY:
        if data.weekday is None:
            raise ValueError("Для weekly укажите weekday")
        return

    if data.frequency == ScheduledMeetingFrequency.MONTHLY:
        if data.monthly_mode is None:
            raise ValueError("Для monthly укажите monthly_mode")
        if data.monthly_mode == ScheduledMeetingMonthlyMode.BY_DAY_OF_MONTH:
            if data.day_of_month is None or not 1 <= data.day_of_month <= 31:
                raise ValueError("day_of_month должен быть в диапазоне 1–31")
            return
        if data.monthly_mode == ScheduledMeetingMonthlyMode.BY_WEEKDAY_POSITION:
            if data.weekday is None or data.weekday_position is None:
                raise ValueError(
                    "Для by_weekday_position укажите weekday и weekday_position"
                )
            return

    if data.frequency in {ScheduledMeetingFrequency.DAILY, ScheduledMeetingFrequency.YEARLY}:
        return

    raise ValueError(f"Неизвестная частота: {data.frequency}")


def build_recurrence_rule(data: RecurrenceInput) -> dict[str, Any]:
    validate_recurrence_input(data)
    rule: dict[str, Any] = {
        "frequency": data.frequency.value,
        "interval": data.interval,
        "time": data.time_local.strftime("%H:%M"),
        "duration_minutes": data.duration_minutes,
        "series_start_date": data.series_start_date.isoformat(),
        "series_end_date": data.series_end_date.isoformat(),
    }
    if data.monthly_mode is not None:
        rule["monthly_mode"] = data.monthly_mode.value
    if data.day_of_month is not None:
        rule["day_of_month"] = data.day_of_month
    if data.weekday is not None:
        rule["weekday"] = data.weekday.value
    if data.weekday_position is not None:
        rule["weekday_position"] = data.weekday_position.value
    return rule


def format_recurrence_label(data: RecurrenceInput) -> str:
    validate_recurrence_input(data)
    time_label = format_time_label(data.time_local)

    if data.frequency == ScheduledMeetingFrequency.DAILY:
        # Ежедневные серии в УД — только рабочие дни (пн–пт).
        if data.interval == 1:
            prefix = "ежедневно по будням"
        else:
            prefix = f"каждые {data.interval} раб. дн."
        return f"{prefix}, {time_label}"

    if data.frequency == ScheduledMeetingFrequency.WEEKLY:
        weekday_label = WEEKDAY_LABELS_RU[data.weekday]  # type: ignore[index]
        if data.interval == 1:
            prefix = "еженедельно"
        elif data.interval == 2:
            prefix = "раз в две недели"
        else:
            prefix = f"раз в {data.interval} недели"
        return f"{prefix}, {weekday_label} {time_label}"

    if data.frequency == ScheduledMeetingFrequency.MONTHLY:
        if data.monthly_mode == ScheduledMeetingMonthlyMode.BY_DAY_OF_MONTH:
            if data.interval == 1:
                prefix = "ежемесячно"
            elif data.interval == 3:
                prefix = "ежеквартально"
            else:
                prefix = f"каждые {data.interval} мес."
            return f"{prefix}, {data.day_of_month} число {time_label}"

        weekday_label = WEEKDAY_LABELS_RU[data.weekday]  # type: ignore[index]
        position_label = WEEKDAY_POSITION_LABELS_RU[data.weekday_position]  # type: ignore[index]
        if data.interval == 1:
            prefix = "ежемесячно"
        elif data.interval == 3:
            prefix = "ежеквартально"
        else:
            prefix = f"каждые {data.interval} мес."
        return f"{prefix}, {position_label} {weekday_label} {time_label}"

    if data.frequency == ScheduledMeetingFrequency.YEARLY:
        prefix = "ежегодно" if data.interval == 1 else f"раз в {data.interval} года"
        return f"{prefix}, {time_label}"

    raise ValueError(f"Неизвестная частота: {data.frequency}")


def _clamp_day_of_month(year: int, month: int, day_of_month: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day_of_month, last_day))


def _weekday_dates_in_month(
    year: int,
    month: int,
    weekday: ScheduledMeetingWeekday,
) -> list[date]:
    iso_weekday = WEEKDAY_TO_ISO[weekday]
    days: list[date] = []
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        current = date(year, month, day)
        if current.weekday() == iso_weekday:
            days.append(current)
    return days


def _monthly_weekday_position_date(
    year: int,
    month: int,
    *,
    weekday: ScheduledMeetingWeekday,
    weekday_position: ScheduledMeetingWeekdayPosition,
) -> date | None:
    matches = _weekday_dates_in_month(year, month, weekday)
    if not matches:
        return None
    if weekday_position == ScheduledMeetingWeekdayPosition.LAST:
        return matches[-1]
    index = {
        ScheduledMeetingWeekdayPosition.FIRST: 0,
        ScheduledMeetingWeekdayPosition.SECOND: 1,
        ScheduledMeetingWeekdayPosition.THIRD: 2,
        ScheduledMeetingWeekdayPosition.FOURTH: 3,
    }.get(weekday_position)
    if index is None or index >= len(matches):
        return None
    return matches[index]


def iter_occurrence_dates(
    data: RecurrenceInput,
    *,
    range_start: date | None = None,
    range_end: date | None = None,
) -> list[date]:
    validate_recurrence_input(data)
    start = max(data.series_start_date, range_start or data.series_start_date)
    end = min(data.series_end_date, range_end or data.series_end_date)
    if end < start:
        return []

    dates: list[date] = []

    if data.frequency == ScheduledMeetingFrequency.DAILY:
        # Только пн–пт; interval считает рабочие дни, не календарные.
        anchor = first_weekday_on_or_after(data.series_start_date)
        current = max(start, anchor)
        if is_weekend(current):
            current = first_weekday_on_or_after(current)
        while current <= end:
            if not is_weekend(current):
                biz_index = _business_day_index(anchor, current)
                if biz_index >= 0 and biz_index % data.interval == 0:
                    dates.append(current)
            current += timedelta(days=1)
        return dates

    if data.frequency == ScheduledMeetingFrequency.WEEKLY:
        if data.weekday is None:
            return []
        target_iso = WEEKDAY_TO_ISO[data.weekday]
        current = start
        while current.weekday() != target_iso:
            current += timedelta(days=1)
            if current > end:
                return dates
        anchor = data.series_start_date
        while anchor.weekday() != target_iso:
            anchor += timedelta(days=1)
        if current >= anchor:
            weeks_between = (current - anchor).days // 7
            remainder = weeks_between % data.interval
            if remainder:
                current += timedelta(days=(data.interval - remainder) * 7)
        while current <= end:
            _append_adjusted_occurrence(dates, current, range_start=start, range_end=end)
            current += timedelta(days=7 * data.interval)
        return _dedupe_sorted_dates(dates)

    if data.frequency == ScheduledMeetingFrequency.MONTHLY:
        month_cursor = date(start.year, start.month, 1)
        end_month = date(end.year, end.month, 1)
        while month_cursor <= end_month:
            months_since_start = (month_cursor.year - data.series_start_date.year) * 12 + (
                month_cursor.month - data.series_start_date.month
            )
            if months_since_start >= 0 and months_since_start % data.interval == 0:
                occurrence: date | None = None
                if data.monthly_mode == ScheduledMeetingMonthlyMode.BY_DAY_OF_MONTH:
                    occurrence = _clamp_day_of_month(
                        month_cursor.year,
                        month_cursor.month,
                        data.day_of_month or 1,
                    )
                elif (
                    data.monthly_mode == ScheduledMeetingMonthlyMode.BY_WEEKDAY_POSITION
                    and data.weekday is not None
                    and data.weekday_position is not None
                ):
                    occurrence = _monthly_weekday_position_date(
                        month_cursor.year,
                        month_cursor.month,
                        weekday=data.weekday,
                        weekday_position=data.weekday_position,
                    )
                if occurrence is not None:
                    _append_adjusted_occurrence(
                        dates,
                        occurrence,
                        range_start=start,
                        range_end=end,
                    )
            if month_cursor.month == 12:
                month_cursor = date(month_cursor.year + 1, 1, 1)
            else:
                month_cursor = date(month_cursor.year, month_cursor.month + 1, 1)
        return _dedupe_sorted_dates(dates)

    if data.frequency == ScheduledMeetingFrequency.YEARLY:
        year = start.year
        while year <= end.year:
            years_since_start = year - data.series_start_date.year
            if years_since_start >= 0 and years_since_start % data.interval == 0:
                try:
                    occurrence = date(
                        year,
                        data.series_start_date.month,
                        data.series_start_date.day,
                    )
                except ValueError:
                    # 29.02 в невисокосном — последний день февраля
                    occurrence = _clamp_day_of_month(
                        year,
                        data.series_start_date.month,
                        data.series_start_date.day,
                    )
                _append_adjusted_occurrence(
                    dates,
                    occurrence,
                    range_start=start,
                    range_end=end,
                )
            year += 1
        return _dedupe_sorted_dates(dates)

    raise ValueError(f"Неизвестная частота: {data.frequency}")


def _append_adjusted_occurrence(
    dates: list[date],
    raw: date,
    *,
    range_start: date,
    range_end: date,
) -> None:
    """Добавляет дату с переносом сб/вс → пт; исходная дата могла быть в диапазоне."""
    adjusted = adjust_weekend_to_preceding_weekday(raw)
    if range_start <= raw <= range_end or range_start <= adjusted <= range_end:
        dates.append(adjusted)


def _dedupe_sorted_dates(dates: list[date]) -> list[date]:
    if not dates:
        return dates
    unique: list[date] = []
    seen: set[date] = set()
    for item in dates:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    unique.sort()
    return unique


def occurrence_slot_bounds(
    occurrence_date: date,
    *,
    time_local: time,
    duration_minutes: int,
    timezone_name: str,
) -> tuple[datetime, datetime]:
    tz = ZoneInfo(timezone_name)
    slot_start = datetime.combine(occurrence_date, time_local, tzinfo=tz)
    slot_end = slot_start + timedelta(minutes=duration_minutes)
    return slot_start, slot_end


def apply_recurrence_payload_to_meeting(
    meeting,
    recurrence,
) -> None:
    from app.schemas.scheduled_meeting import ScheduledMeetingRecurrencePayload

    if not isinstance(recurrence, ScheduledMeetingRecurrencePayload):
        raise TypeError("recurrence must be ScheduledMeetingRecurrencePayload")

    meeting.frequency = recurrence.frequency
    meeting.interval = recurrence.interval
    meeting.time_local = recurrence.time_local
    meeting.duration_minutes = recurrence.duration_minutes
    meeting.monthly_mode = recurrence.monthly_mode
    meeting.day_of_month = recurrence.day_of_month
    meeting.weekday = recurrence.weekday
    meeting.weekday_position = recurrence.weekday_position
    if recurrence.series_end_date is not None:
        meeting.series_end_date = recurrence.series_end_date
