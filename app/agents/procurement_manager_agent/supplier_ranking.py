"""Deterministic top-N supplier offer ranking (deadline → cost/overpay → speed)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.agents.procurement_manager_agent.material_bank import MaterialBankStore, get_material_bank
from app.agents.procurement_manager_agent.optimize import (
    OPTIMIZATION_FORMULA,
    optimize_supplier_offers,
)

# Kept for backward-compatible imports / informational price+coverage utility.
PRICE_WEIGHT = Decimal("0.55")
COVERAGE_WEIGHT = Decimal("0.45")
SHORTFALL_PENALTY_WEIGHT = Decimal("0.05")

SCORE_FORMULA = OPTIMIZATION_FORMULA

_ZERO = Decimal("0")


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _available_qty(offering: dict[str, Any]) -> Decimal | None:
    """Prefer available_quantity; accept available_qty alias."""
    qty = _dec(offering.get("available_quantity"))
    if qty is None:
        qty = _dec(offering.get("available_qty"))
    return qty


def collect_supplier_offers(
    nomenclature_id: str,
    *,
    bank: MaterialBankStore | None = None,
) -> list[dict[str, Any]]:
    """Raw active-supplier offerings for a nomenclature (price + capacity)."""
    store = bank or get_material_bank()
    key = nomenclature_id.strip().casefold()
    if not key:
        return []
    offers: list[dict[str, Any]] = []
    for supplier in store.active_suppliers():
        for offering in supplier.get("offerings") or []:
            if not isinstance(offering, dict):
                continue
            nom = str(offering.get("nomenclature_id") or "").strip()
            if nom.casefold() != key:
                continue
            price = _dec(offering.get("unit_price"))
            available = _available_qty(offering)
            if price is None or price < 0 or available is None or available <= 0:
                continue
            row: dict[str, Any] = {
                "supplier_id": str(supplier.get("supplier_id") or ""),
                "supplier_name": str(supplier.get("name") or supplier.get("supplier_id") or ""),
                "nomenclature_id": nom,
                "nomenclature_name": offering.get("nomenclature_name"),
                "unit_price": price,
                "available_qty": available,
                "unit": offering.get("unit") or "шт",
                "lead_time_days": offering.get("lead_time_days"),
            }
            for lot_key in ("lot_size", "pack_qty", "pack_size", "min_order_qty"):
                lot_val = _dec(offering.get(lot_key))
                if lot_val is not None and lot_val > 0:
                    row[lot_key] = lot_val
            offers.append(row)
    return offers


def rank_supplier_offers(
    nomenclature_id: str,
    need_qty: Decimal,
    *,
    bank: MaterialBankStore | None = None,
    top_n: int = 3,
    required_date: Any = None,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """
    Rank suppliers for a nomenclature need.

    Priority gates: meets_deadline → minimize total_cost (incl. overpay) →
    shorter lead_time → lower unit_price.
    """
    need = need_qty if need_qty > 0 else _ZERO
    if need <= 0 or top_n <= 0:
        return []

    raw = collect_supplier_offers(nomenclature_id, bank=bank)
    if not raw:
        return []

    return optimize_supplier_offers(
        need,
        raw,
        required_date=required_date,
        today=today,
        top_n=top_n,
    )


def price_bounds_from_offers(offers: list[dict[str, Any]]) -> tuple[Decimal | None, Decimal | None]:
    """Min/max unit_price from ranked or raw offers (same source as table bounds)."""
    prices = [item["unit_price"] for item in offers if item.get("unit_price") is not None]
    if not prices:
        return None, None
    return min(prices), max(prices)


__all__ = [
    "COVERAGE_WEIGHT",
    "PRICE_WEIGHT",
    "SCORE_FORMULA",
    "SHORTFALL_PENALTY_WEIGHT",
    "collect_supplier_offers",
    "price_bounds_from_offers",
    "rank_supplier_offers",
]
