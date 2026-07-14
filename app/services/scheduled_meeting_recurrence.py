from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Any

from app.models.enums import (
    ScheduledMeetingFrequency,
    ScheduledMeetingMonthlyMode,
    ScheduledMeetingWeekday,
    ScheduledMeetingWeekdayPosition,
)

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
        prefix = "ежедневно" if data.interval == 1 else f"каждые {data.interval} дн."
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
