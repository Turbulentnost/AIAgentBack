"""СТО-28-020 date helpers: workdays, §6.11.3, §6.11.4 (ported from contour4)."""

from __future__ import annotations

from datetime import date, datetime, timedelta


def parse_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def add_workdays(start: date, days: int) -> date:
    """Сдвиг на N рабочих дней (пн–пт). days может быть отрицательным."""
    if days == 0:
        return start
    cur = start
    step = 1 if days > 0 else -1
    target = abs(days)
    added = 0
    while added < target:
        cur += timedelta(days=step)
        if cur.weekday() < 5:
            added += 1
    return cur


def calc_payment_planned_date(
    production_need_date: date,
    *,
    receipt_days: int = 1,
    otk_days: int = 3,
    delivery_days: int,
    payment_cycle_days: int = 1,
) -> date:
    """
    СТО-28-020 §6.11.4:
    дата потребности − 1 р.д. (оприходование) − 3 р.д. (ОТК)
    − срок поставки − 1 р.д. (цикл оплаты).
    """
    d = add_workdays(production_need_date, -receipt_days)
    d = add_workdays(d, -otk_days)
    d = d - timedelta(days=max(0, delivery_days))
    d = add_workdays(d, -payment_cycle_days)
    return d


def validate_payment_date_not_before_next_workday(
    payment_date: date, approval_start: date
) -> bool:
    """СТО-28-020 §6.11.3: желаемая дата оплаты ≥ следующий рабочий день."""
    min_date = add_workdays(approval_start, 1)
    return payment_date >= min_date


def check_lead_time_mismatch(
    delivery_days: int,
    lead_time_vvz_days: int,
    *,
    threshold_workdays: int = 14,
) -> bool:
    """
    СТО-14-040 §6.9: сверка срока из счёта/КП с ВВЗ (плечо подвоза).
    При расхождении более 14 рабочих дней — флаг; ВВЗ ассистент не меняет.
    """
    return abs(int(delivery_days) - int(lead_time_vvz_days)) > threshold_workdays


__all__ = [
    "add_workdays",
    "calc_payment_planned_date",
    "check_lead_time_mismatch",
    "parse_date",
    "validate_payment_date_not_before_next_workday",
]
