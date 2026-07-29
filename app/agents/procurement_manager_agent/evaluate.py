"""Unified evaluation: optimization ranking (deadline→cost→speed) + quote comparison."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.agents.procurement_manager_agent.material_bank import MaterialBankStore, get_material_bank
from app.agents.procurement_manager_agent.optimize import optimize_case_coverage
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
    required_date: Any = None,
) -> list[dict[str, Any]]:
    """Top suppliers for one nomenclature need (deterministic ranking)."""
    return rank_supplier_offers(
        nomenclature_id,
        need_qty,
        bank=bank or get_material_bank(),
        top_n=top_n,
        required_date=required_date,
    )


def evaluate_case_positions(
    positions: list[dict[str, Any]],
    *,
    bank: MaterialBankStore | None = None,
    top_n: int = 3,
    case_required_date: Any = None,
    use_bank_first: bool = True,
) -> dict[str, Any]:
    """
    Case-level evaluation: bank-first remainder, then deadline→cost→speed picks.

    One primary supplier per line (best optimization rank).
    Split only when coverable_qty < need.
    """
    store = bank or get_material_bank()
    if use_bank_first:
        plan = optimize_case_coverage(
            positions,
            bank=store,
            top_n=top_n,
            case_required_date=case_required_date,
        )
        unit_by_line: dict[str, str] = {}
        for position in positions:
            lid = str(position.get("line_id") or position.get("id") or "")
            if lid:
                unit_by_line[lid] = str(position.get("unit") or "шт")
        by_line: list[dict[str, Any]] = []
        supplier_ids: list[str] = []
        comparable = 0
        bounds_by_nom = supplier_price_bounds(store)
        for line in plan.get("lines") or []:
            nom = str(line.get("nomenclature_id") or "").strip()
            try:
                need = Decimal(str(line.get("needed_quantity") or 0))
            except Exception:
                need = Decimal("0")
            if not nom or need <= 0:
                continue
            top = list(line.get("top_suppliers") or [])
            # If warehouse covered 100%, still surface suppliers for the full need
            # so UI top-3 is not empty when remainder is 0.
            if not top:
                top = evaluate_nomenclature(
                    nom,
                    need,
                    bank=store,
                    top_n=top_n,
                    required_date=line.get("required_date") or case_required_date,
                )
            if len(top) >= 2:
                comparable += 1
            primary = top[0] if top else None
            if primary:
                supplier_ids.append(str(primary["supplier_id"]))
            bounds = bounds_by_nom.get(nom.casefold())
            price_min = bounds["price_min"] if bounds else None
            offers = collect_supplier_offers(nom, bank=store)
            from_supplier = Decimal(str(line.get("supplier_remainder") or need))
            estimate = estimate_nomenclature_amount(
                need,
                price_min=price_min,
                coverage_source=line.get("coverage_source") or "supplier",
                from_supplier=from_supplier if line.get("coverage_source") else need,
                offers=offers,
            )
            line_id = str(line.get("line_id") or nom)
            by_line.append(
                {
                    "line_id": line_id,
                    "nomenclature_id": nom,
                    "nomenclature_name": line.get("nomenclature_name") or nom,
                    "need_qty": str(need),
                    "unit": unit_by_line.get(line_id) or "шт",
                    "required_date": line.get("required_date"),
                    "from_warehouse": line.get("from_warehouse"),
                    "supplier_remainder": line.get("supplier_remainder"),
                    "price_min": str(price_min) if price_min is not None else None,
                    "price_max": (
                        str(bounds["price_max"])
                        if bounds and bounds.get("price_max") is not None
                        else None
                    ),
                    "avg_unit_price": (
                        str(estimate.avg_unit_price)
                        if estimate.avg_unit_price is not None
                        else None
                    ),
                    "estimated_amount": (
                        str(estimate.amount) if estimate.amount is not None else None
                    ),
                    "overpay": str(
                        primary["overpay"] if primary else (estimate.overpay or "0.00")
                    ),
                    "amount_source": estimate.source,
                    "top_suppliers": top,
                    "recommended_supplier_id": (
                        primary["supplier_id"] if primary else None
                    ),
                    "meets_deadline": primary.get("meets_deadline") if primary else None,
                    "lead_time_days": primary.get("lead_time_days") if primary else None,
                    "optimization_rank": (
                        primary.get("optimization_rank") if primary else None
                    ),
                    "optimization_reason": (
                        primary.get("optimization_reason") if primary else None
                    ),
                    "deadline_risk": bool(primary.get("deadline_risk")) if primary else False,
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
            "comparison_complete": bool(by_line)
            and all(line.get("top_suppliers") for line in by_line),
            "score_formula": SCORE_FORMULA,
            "amount_formula": AMOUNT_FORMULA,
            "optimization_plan": {
                "picks": plan.get("picks") or [],
                "summary": plan.get("summary") or {},
            },
            "kpi_flags": {
                "comparable_quotes_ok": comparable >= max(1, len(by_line) // 2)
                if by_line
                else False,
                "comparison_complete": bool(by_line)
                and all(line.get("top_suppliers") for line in by_line),
                "supplier_confirmed": False,
            },
        }

    bounds_by_nom = supplier_price_bounds(store)
    by_line = []
    supplier_ids = []
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
        required = position.get("required_date") or case_required_date
        top = evaluate_nomenclature(
            nom, need, bank=store, top_n=top_n, required_date=required
        )
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
                "required_date": required,
                "price_min": str(price_min) if price_min is not None else None,
                "price_max": (
                    str(bounds["price_max"])
                    if bounds and bounds.get("price_max") is not None
                    else None
                ),
                "avg_unit_price": (
                    str(estimate.avg_unit_price)
                    if estimate.avg_unit_price is not None
                    else None
                ),
                "estimated_amount": (
                    str(estimate.amount) if estimate.amount is not None else None
                ),
                "overpay": str(
                    primary["overpay"] if primary else (estimate.overpay or "0.00")
                ),
                "amount_source": estimate.source,
                "top_suppliers": top,
                "recommended_supplier_id": (
                    primary["supplier_id"] if primary else None
                ),
                "meets_deadline": primary.get("meets_deadline") if primary else None,
                "lead_time_days": primary.get("lead_time_days") if primary else None,
                "optimization_rank": (
                    primary.get("optimization_rank") if primary else None
                ),
                "optimization_reason": (
                    primary.get("optimization_reason") if primary else None
                ),
                "deadline_risk": bool(primary.get("deadline_risk")) if primary else False,
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


def _supplier_source(item: dict[str, Any]) -> str:
    return str(item.get("source") or "internal").strip().casefold()


def _is_trusted_supplier(item: dict[str, Any]) -> bool:
    """1C / internal bank suppliers are trusted without HITL."""
    source = _supplier_source(item)
    if source in {"1c", "internal"}:
        return True
    evidence = item.get("evidence") or []
    return any(str(row).startswith("bank:") for row in evidence)


def _is_web_supplier(item: dict[str, Any]) -> bool:
    return _supplier_source(item) == "web"


def build_trusted_cost_estimate(
    positions: list[dict[str, Any]],
    *,
    candidates: list[dict[str, Any]] | None = None,
    web_candidates: list[dict[str, Any]] | None = None,
    web_approved: bool = False,
    bank: MaterialBankStore | None = None,
    case_required_date: Any = None,
) -> dict[str, Any]:
    """Смета from trusted 1C/internal (+ bank) and HITL-approved web only.

    Unapproved web suppliers never contribute prices or recommended ids.
    """
    store = bank or get_material_bank()
    base = evaluate_case_positions(
        positions,
        bank=store,
        top_n=3,
        case_required_date=case_required_date,
        use_bank_first=True,
    )
    pool = list(candidates or [])
    if web_candidates:
        seen = {
            str(item.get("supplier_id") or item.get("tax_id") or "")
            for item in pool
            if isinstance(item, dict)
        }
        for item in web_candidates:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("supplier_id") or item.get("tax_id") or "")
            if sid and sid in seen:
                continue
            if sid:
                seen.add(sid)
            pool.append(item)

    trusted = [item for item in pool if isinstance(item, dict) and _is_trusted_supplier(item)]
    approved_web = (
        [item for item in pool if isinstance(item, dict) and _is_web_supplier(item)]
        if web_approved
        else []
    )
    # Web unit prices enrich the estimate only after HITL shortlist approval.
    web_offers: list[dict[str, Any]] = []
    if web_approved:
        for item in approved_web:
            price = item.get("unit_price")
            if price is None:
                price = item.get("approx_cost")
            if price is None:
                continue
            try:
                unit_price = Decimal(str(price))
            except Exception:
                continue
            if unit_price < 0:
                continue
            web_offers.append(
                {
                    "supplier_id": str(item.get("supplier_id")),
                    "supplier_name": str(item.get("name") or item.get("supplier_id")),
                    "unit_price": str(unit_price),
                    "source": "web",
                    "coverable_qty": str(item.get("coverable_qty") or "999999"),
                    "lead_time_days": int(
                        item.get("delivery_days") or item.get("lead_time_days") or 7
                    ),
                    "approved_web": True,
                }
            )

    lines_out: list[dict[str, Any]] = []
    total = Decimal("0")
    any_total = False
    for line in base.get("lines") or []:
        # Bank / 1C / internal offers are trusted; strip any web until HITL.
        top = [
            offer
            for offer in (line.get("top_suppliers") or [])
            if str(offer.get("source") or "internal").casefold() != "web"
        ]
        if web_approved:
            for offer in web_offers:
                if any(
                    str(existing.get("supplier_id")) == offer["supplier_id"]
                    for existing in top
                ):
                    continue
                top.append(offer)
        amount = line.get("estimated_amount")
        try:
            if amount is not None:
                total += Decimal(str(amount))
                any_total = True
        except Exception:
            pass
        lines_out.append(
            {
                **line,
                "top_suppliers": top[:5],
                "recommended_supplier_id": (
                    top[0]["supplier_id"] if top else line.get("recommended_supplier_id")
                ),
                "estimate_sources": (
                    ["1c", "internal", "bank"]
                    + (["web_approved"] if web_approved and approved_web else [])
                ),
            }
        )

    recommended = []
    for line in lines_out:
        sid = line.get("recommended_supplier_id")
        if sid:
            recommended.append(str(sid))
    recommended = list(dict.fromkeys(recommended))
    return {
        "lines": lines_out,
        "recommended_supplier_ids": recommended,
        "primary_supplier_id": recommended[0] if recommended else base.get("primary_supplier_id"),
        "total_estimated_amount": str(total) if any_total else None,
        "web_approved": web_approved,
        "trusted_supplier_ids": [
            str(item.get("supplier_id")) for item in trusted if item.get("supplier_id")
        ],
        "approved_web_supplier_ids": [
            str(item.get("supplier_id")) for item in approved_web if item.get("supplier_id")
        ],
        "excluded_unapproved_web": not web_approved,
        "amount_formula": base.get("amount_formula") or AMOUNT_FORMULA,
        "kpi_flags": {
            **dict(base.get("kpi_flags") or {}),
            "supplier_confirmed": bool(web_approved or recommended),
            "web_included": bool(web_approved and approved_web),
        },
    }


__all__ = [
    "build_trusted_cost_estimate",
    "evaluate_case_positions",
    "evaluate_nomenclature",
    "evaluate_quotes",
]
