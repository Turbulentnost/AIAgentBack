"""Delivery schedule formula — planned arrival vs required_date.

Extracted so optimizer and UI schedule edits share one definition.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

DELIVERY_SCHEDULE_FORMULA = (
    "planned_arrival = supplier_ship_date OR (as_of + supplier_lead_days); "
    "meets_deadline = planned_arrival <= required_date "
    "(если required_date задан; без срока — True; без lead/ship при сроке — None)."
)


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def planned_arrival(
    *,
    supplier_lead_days: int | None = None,
    supplier_ship_date: date | str | None = None,
    as_of: date | None = None,
) -> date | None:
    """Compute planned warehouse arrival from ship date or lead days."""
    ship = _parse_date(supplier_ship_date)
    if ship is not None:
        return ship
    if supplier_lead_days is None:
        return None
    try:
        days = int(supplier_lead_days)
    except (TypeError, ValueError):
        return None
    if days < 0:
        return None
    return (as_of or date.today()) + timedelta(days=days)


def offer_meets_deadline(
    required_date: Any,
    lead_time_days: int | None,
    *,
    today: date | None = None,
    supplier_ship_date: date | str | None = None,
) -> bool | None:
    """
    True if planned arrival is on/before required_date.

    None — cannot verify (no lead/ship while deadline is set).
    True when no required_date (no deadline constraint).
    """
    req = _parse_date(required_date)
    if req is None:
        return True
    arrival = planned_arrival(
        supplier_lead_days=lead_time_days,
        supplier_ship_date=supplier_ship_date,
        as_of=today or date.today(),
    )
    if arrival is None:
        return None
    return arrival <= req


def compute_schedule(
    *,
    required_date: Any = None,
    lead_days: int | None = None,
    ship_date: date | str | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Return schedule fields for API/UI."""
    today = as_of or date.today()
    arrival = planned_arrival(
        supplier_lead_days=lead_days,
        supplier_ship_date=ship_date,
        as_of=today,
    )
    meets = offer_meets_deadline(
        required_date,
        lead_days,
        today=today,
        supplier_ship_date=ship_date,
    )
    return {
        "supplier_lead_days": lead_days,
        "supplier_ship_date": (
            ship_date.isoformat()
            if isinstance(ship_date, date)
            else (str(ship_date)[:10] if ship_date else None)
        ),
        "planned_arrival": arrival.isoformat() if arrival else None,
        "required_date": (
            _parse_date(required_date).isoformat() if _parse_date(required_date) else None
        ),
        "meets_deadline": meets,
        "deadline_risk": meets is False,
        "formula": DELIVERY_SCHEDULE_FORMULA,
    }


__all__ = [
    "DELIVERY_SCHEDULE_FORMULA",
    "compute_schedule",
    "offer_meets_deadline",
    "planned_arrival",
]
