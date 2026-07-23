"""Deterministic top-N supplier offer ranking by price + coverage utility."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.agents.procurement_manager_agent.material_bank import MaterialBankStore, get_material_bank

PRICE_WEIGHT = Decimal("0.55")
COVERAGE_WEIGHT = Decimal("0.45")
# Mild penalty when coverable_qty < need_qty (applied to final score).
SHORTFALL_PENALTY_WEIGHT = Decimal("0.05")
_SCORE_QUANT = Decimal("0.0001")
_MONEY_QUANT = Decimal("0.01")
_ONE = Decimal("1")
_ZERO = Decimal("0")

SCORE_FORMULA = (
    "coverable_qty = min(need_qty, available_qty); "
    "coverage_ratio = coverable_qty / need_qty; "
    "price_score = 1 if max=min else (max_price - unit_price) / (max_price - min_price); "
    "coverage_score = coverage_ratio; "
    "score = 0.55 * price_score + 0.45 * coverage_score "
    "- 0.05 * (1 - coverage_ratio) [shortfall penalty]; "
    "cost = coverable_qty * unit_price."
)


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _round_score(value: Decimal) -> Decimal:
    return value.quantize(_SCORE_QUANT, rounding=ROUND_HALF_UP)


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _available_qty(offering: dict[str, Any]) -> Decimal | None:
    """Prefer available_quantity; accept available_qty alias."""
    qty = _dec(offering.get("available_quantity"))
    if qty is None:
        qty = _dec(offering.get("available_qty"))
    return qty


def _build_reason(
    *,
    unit_price: Decimal,
    min_price: Decimal,
    coverage_ratio: Decimal,
) -> str:
    parts: list[str] = []
    if unit_price == min_price:
        parts.append("дешевле")
    if coverage_ratio >= _ONE:
        parts.append("закрывает 100%")
    else:
        pct = int((coverage_ratio * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP))
        parts.append(f"частично {pct}%")
    return ", ".join(parts) if parts else "кандидат"


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
) -> list[dict[str, Any]]:
    """
    Rank suppliers for a nomenclature need by price+coverage utility.

    Higher score is better. Ties broken by lower cost, then higher coverable_qty,
    then supplier_id.
    """
    need = need_qty if need_qty > 0 else _ZERO
    if need <= 0 or top_n <= 0:
        return []

    raw = collect_supplier_offers(nomenclature_id, bank=bank)
    if not raw:
        return []

    prices = [item["unit_price"] for item in raw]
    min_price = min(prices)
    max_price = max(prices)
    price_span = max_price - min_price

    ranked: list[dict[str, Any]] = []
    for item in raw:
        unit_price: Decimal = item["unit_price"]
        available_qty: Decimal = item["available_qty"]
        coverable_qty = min(need, available_qty)
        coverage_ratio = (coverable_qty / need) if need > 0 else _ZERO
        if price_span == 0:
            price_score = _ONE
        else:
            price_score = (max_price - unit_price) / price_span
        coverage_score = coverage_ratio
        shortfall_penalty = SHORTFALL_PENALTY_WEIGHT * (_ONE - coverage_ratio)
        score = PRICE_WEIGHT * price_score + COVERAGE_WEIGHT * coverage_score - shortfall_penalty
        cost = coverable_qty * unit_price
        ranked.append(
            {
                "rank": 0,
                "supplier_id": item["supplier_id"],
                "supplier_name": item["supplier_name"],
                "nomenclature_id": item["nomenclature_id"],
                "nomenclature_name": item.get("nomenclature_name"),
                "unit_price": _round_money(unit_price),
                "available_qty": available_qty,
                "coverable_qty": coverable_qty,
                "coverage_ratio": _round_score(coverage_ratio),
                "coverage_cost": _round_money(cost),
                "price_score": _round_score(price_score),
                "coverage_score": _round_score(coverage_score),
                "score": _round_score(score),
                "reason": _build_reason(
                    unit_price=unit_price,
                    min_price=min_price,
                    coverage_ratio=coverage_ratio,
                ),
                "unit": item.get("unit") or "шт",
                "lead_time_days": item.get("lead_time_days"),
            }
        )

    ranked.sort(
        key=lambda row: (
            -row["score"],
            row["coverage_cost"],
            -row["coverable_qty"],
            row["supplier_id"],
        )
    )
    top = ranked[:top_n]
    for index, row in enumerate(top, start=1):
        row["rank"] = index
    return top


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
