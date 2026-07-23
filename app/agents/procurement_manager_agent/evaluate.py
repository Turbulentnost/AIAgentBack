"""Unified evaluation: supplier_ranking (price+coverage) + quote comparison."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.agents.procurement_manager_agent.material_bank import MaterialBankStore, get_material_bank
from app.agents.procurement_manager_agent.pricing import (
    AMOUNT_FORMULA,
    estimate_nomenclature_amount,
    supplier_price_bounds,
)
from app.agents.procurement_manager_agent.schemas import (
    ComparisonWeights,
    QuoteComparison,
    SupplierQuote,
)
from app.agents.procurement_manager_agent.scoring import compare_quotes
from app.agents.procurement_manager_agent.supplier_ranking import (
    SCORE_FORMULA,
    collect_supplier_offers,
    rank_supplier_offers,
)


def evaluate_nomenclature(
    nomenclature_id: str,
    need_qty: Decimal,
    *,
    bank: MaterialBankStore | None = None,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """Top suppliers for one nomenclature need (deterministic ranking)."""
    return rank_supplier_offers(
        nomenclature_id,
        need_qty,
        bank=bank or get_material_bank(),
        top_n=top_n,
    )


def evaluate_case_positions(
    positions: list[dict[str, Any]],
    *,
    bank: MaterialBankStore | None = None,
    top_n: int = 3,
) -> dict[str, Any]:
    """
    Case-level evaluation: per-line top suppliers + recommended supplier picks.

    One primary supplier per line (best score). Split only when coverable_qty < need.
    """
    store = bank or get_material_bank()
    bounds_by_nom = supplier_price_bounds(store)
    by_line: list[dict[str, Any]] = []
    supplier_ids: list[str] = []
    comparable = 0
    for position in positions:
        nom = str(
            position.get("nomenclature_id")
            or position.get("nomenclature_code")
            or ""
        ).strip()
        try:
            need = Decimal(str(position.get("quantity") or position.get("qty") or 0))
        except Exception:
            need = Decimal("0")
        if not nom or need <= 0:
            continue
        top = evaluate_nomenclature(nom, need, bank=store, top_n=top_n)
        if len(top) >= 2:
            comparable += 1
        primary = top[0] if top else None
        if primary:
            supplier_ids.append(str(primary["supplier_id"]))
        bounds = bounds_by_nom.get(nom.casefold())
        price_min = bounds["price_min"] if bounds else None
        offers = collect_supplier_offers(nom, bank=store)
        estimate = estimate_nomenclature_amount(
            need,
            price_min=price_min,
            coverage_source="supplier",
            from_supplier=need,
            offers=offers,
        )
        by_line.append(
            {
                "line_id": str(position.get("line_id") or position.get("id") or nom),
                "nomenclature_id": nom,
                "nomenclature_name": position.get("nomenclature_name")
                or position.get("name")
                or nom,
                "need_qty": str(need),
                "unit": position.get("unit") or "шт",
                "price_min": str(price_min) if price_min is not None else None,
                "price_max": (
                    str(bounds["price_max"]) if bounds and bounds.get("price_max") is not None else None
                ),
                "avg_unit_price": (
                    str(estimate.avg_unit_price)
                    if estimate.avg_unit_price is not None
                    else None
                ),
                "estimated_amount": (
                    str(estimate.amount) if estimate.amount is not None else None
                ),
                "overpay": str(estimate.overpay) if estimate.overpay else "0.00",
                "amount_source": estimate.source,
                "top_suppliers": top,
                "recommended_supplier_id": (
                    primary["supplier_id"] if primary else None
                ),
                "split_required": bool(
                    primary and Decimal(str(primary["coverable_qty"])) < need
                ),
            }
        )
    unique_suppliers = list(dict.fromkeys(supplier_ids))
    return {
        "lines": by_line,
        "recommended_supplier_ids": unique_suppliers,
        "primary_supplier_id": unique_suppliers[0] if unique_suppliers else None,
        "comparable_quotes_ok": comparable >= max(1, len(by_line) // 2) if by_line else False,
        "comparison_complete": bool(by_line) and all(
            line.get("top_suppliers") for line in by_line
        ),
        "score_formula": SCORE_FORMULA,
        "amount_formula": AMOUNT_FORMULA,
        "kpi_flags": {
            "comparable_quotes_ok": comparable >= max(1, len(by_line) // 2) if by_line else False,
            "comparison_complete": bool(by_line)
            and all(line.get("top_suppliers") for line in by_line),
            "supplier_confirmed": False,
        },
    }


def evaluate_quotes(
    quotes: list[SupplierQuote],
    *,
    weights: ComparisonWeights | None = None,
) -> QuoteComparison:
    """Compare submitted supplier quotes with unified scoring weights."""
    return compare_quotes(quotes, weights or ComparisonWeights())


__all__ = [
    "evaluate_case_positions",
    "evaluate_nomenclature",
    "evaluate_quotes",
]
