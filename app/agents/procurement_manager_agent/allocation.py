"""Deadline-priority material allocation from the shared bank (warehouses + suppliers)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from app.agents.procurement_manager_agent.material_bank import MaterialBankStore, get_material_bank
from app.agents.procurement_manager_agent.pricing import (
    AMOUNT_FORMULA,
    estimate_nomenclature_amount,
    supplier_price_bounds,
)
from app.agents.procurement_manager_agent.supplier_ranking import collect_supplier_offers

CoverageTone = Literal["ready", "attention", "uncovered"]
CoverageSource = Literal["warehouse", "supplier", "mixed", "none"]

TONE_LABELS: dict[CoverageTone, str] = {
    "ready": "Готов",
    "attention": "Требуют внимания",
    "uncovered": "Полностью необеспечен",
}


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _norm_key(nomenclature_id: str | None, nomenclature_name: str | None = None) -> str:
    nid = (nomenclature_id or "").strip()
    if nid:
        return nid.casefold()
    name = (nomenclature_name or "").strip().casefold()
    return name


def _match_keys(nomenclature_id: str | None, nomenclature_name: str | None = None) -> list[str]:
    """Id first, then name — name fallback covers 1C GUID ids with matching titles."""
    keys: list[str] = []
    nid = (nomenclature_id or "").strip().casefold()
    if nid:
        keys.append(nid)
    name = (nomenclature_name or "").strip().casefold()
    if name and name not in keys:
        keys.append(name)
    return keys


def _source_kind(
    from_warehouse: Decimal,
    from_supplier: Decimal,
) -> CoverageSource:
    has_wh = from_warehouse > 0
    has_sp = from_supplier > 0
    if has_wh and has_sp:
        return "mixed"
    if has_wh:
        return "warehouse"
    if has_sp:
        return "supplier"
    return "none"


def _line_tone(needed: Decimal, covered: Decimal) -> CoverageTone:
    if needed <= 0:
        return "ready"
    if covered <= 0:
        return "uncovered"
    if covered + Decimal("0.000001") >= needed:
        return "ready"
    return "attention"


def _case_tone(line_tones: list[CoverageTone]) -> CoverageTone:
    if not line_tones:
        return "uncovered"
    if all(tone == "ready" for tone in line_tones):
        return "ready"
    if all(tone == "uncovered" for tone in line_tones):
        return "uncovered"
    return "attention"


def demands_from_cases(cases: list[Any]) -> list[dict[str, Any]]:
    """Build demand lines from ORM cases or dict-like case payloads."""
    demands: list[dict[str, Any]] = []
    for case in cases:
        if isinstance(case, dict):
            case_id = str(case.get("id") or case.get("case_id") or "")
            case_required = case.get("required_date")
            positions = case.get("positions") or []
        else:
            case_id = str(getattr(case, "id", "") or "")
            case_required = getattr(case, "required_date", None)
            positions = getattr(case, "positions", None) or []

        for position in positions:
            if isinstance(position, dict):
                if position.get("cancelled"):
                    continue
                line_id = str(position.get("line_id") or position.get("id") or "")
                nomenclature_id = str(position.get("nomenclature_id") or "").strip()
                nomenclature_name = position.get("nomenclature_name")
                quantity = _dec(position.get("quantity"))
                unit = position.get("unit") or "шт"
                required_date = position.get("required_date") or case_required
            else:
                if getattr(position, "cancelled", False):
                    continue
                line_id = str(getattr(position, "line_id", "") or getattr(position, "id", "") or "")
                nomenclature_id = str(getattr(position, "nomenclature_id", "") or "").strip()
                nomenclature_name = getattr(position, "nomenclature_name", None)
                quantity = _dec(getattr(position, "quantity", 0))
                unit = getattr(position, "unit", None) or "шт"
                required_date = getattr(position, "required_date", None) or case_required

            if quantity <= 0:
                continue
            demands.append(
                {
                    "case_id": case_id,
                    "line_id": line_id,
                    "nomenclature_id": nomenclature_id,
                    "nomenclature_name": nomenclature_name,
                    "quantity": quantity,
                    "unit": unit,
                    "required_date": required_date,
                }
            )
    return demands


def _sort_key(demand: dict[str, Any]) -> tuple[Any, ...]:
    required = demand.get("required_date")
    if isinstance(required, str):
        try:
            required = datetime.fromisoformat(required.replace("Z", "+00:00"))
        except ValueError:
            required = None
    # Earliest deadline first; missing dates go last.
    sentinel = datetime.max.replace(tzinfo=None)
    if isinstance(required, datetime):
        sort_dt = required.replace(tzinfo=None) if required.tzinfo else required
    else:
        sort_dt = sentinel
    return (sort_dt, str(demand.get("case_id") or ""), str(demand.get("line_id") or ""))


def allocate_materials_by_deadline(
    cases: list[Any],
    *,
    bank: MaterialBankStore | None = None,
) -> dict[str, Any]:
    """
    Reserve warehouse stock FIFO by required_date, then cover remainder from suppliers.

    Earlier orders lock warehouse qty; later orders see the residual bank only.
    """
    store = bank or get_material_bank()
    warehouses = {item["warehouse_id"]: item for item in store.warehouses()}
    remaining_stock: dict[str, Decimal] = {}
    stock_meta: dict[str, dict[str, Any]] = {}
    for line in store.stock_lines():
        stock_id = str(line.get("stock_id") or "")
        if not stock_id:
            continue
        available = _dec(line.get("quantity")) - _dec(line.get("reserved"))
        if available < 0:
            available = Decimal("0")
        remaining_stock[stock_id] = available
        stock_meta[stock_id] = line

    remaining_supplier: dict[tuple[str, str], Decimal] = {}
    supplier_meta: dict[str, dict[str, Any]] = {}
    for supplier in store.active_suppliers():
        supplier_id = str(supplier.get("supplier_id") or "")
        if not supplier_id:
            continue
        supplier_meta[supplier_id] = supplier
        for offering in supplier.get("offerings") or []:
            if not isinstance(offering, dict):
                continue
            nom = str(offering.get("nomenclature_id") or "").strip()
            if not nom:
                continue
            remaining_supplier[(supplier_id, nom.casefold())] = _dec(
                offering.get("available_quantity", offering.get("available_qty"))
            )

    stock_by_nom: dict[str, list[str]] = {}
    for stock_id, meta in stock_meta.items():
        for key in _match_keys(meta.get("nomenclature_id"), meta.get("nomenclature_name")):
            bucket = stock_by_nom.setdefault(key, [])
            if stock_id not in bucket:
                bucket.append(stock_id)
    for key in stock_by_nom:
        stock_by_nom[key].sort(key=lambda sid: (stock_meta[sid].get("warehouse_id") or "", sid))

    supplier_by_nom: dict[str, list[str]] = {}
    supplier_offering_name: dict[tuple[str, str], str] = {}
    for supplier in store.active_suppliers():
        supplier_id = str(supplier.get("supplier_id") or "")
        for offering in supplier.get("offerings") or []:
            if not isinstance(offering, dict):
                continue
            nom = str(offering.get("nomenclature_id") or "").strip()
            if not nom:
                continue
            name_key = str(offering.get("nomenclature_name") or "").strip().casefold()
            if name_key:
                supplier_offering_name[(supplier_id, nom.casefold())] = name_key

    for (supplier_id, nom_key), qty in remaining_supplier.items():
        if qty <= 0:
            continue
        keys = [nom_key]
        name_key = supplier_offering_name.get((supplier_id, nom_key))
        if name_key and name_key not in keys:
            keys.append(name_key)
        for key in keys:
            bucket = supplier_by_nom.setdefault(key, [])
            if supplier_id not in bucket:
                bucket.append(supplier_id)
    for key in supplier_by_nom:
        supplier_by_nom[key].sort(
            key=lambda sid: (
                next(
                    (
                        int(o.get("lead_time_days") or 99)
                        for o in (supplier_meta[sid].get("offerings") or [])
                        if key
                        in _match_keys(
                            str(o.get("nomenclature_id") or "").strip(),
                            o.get("nomenclature_name"),
                        )
                    ),
                    99,
                ),
                sid,
            )
        )

    demands = sorted(demands_from_cases(cases), key=_sort_key)
    line_results: list[dict[str, Any]] = []
    case_lines: dict[str, list[dict[str, Any]]] = {}

    need_qty_total = Decimal("0")
    covered_qty_total = Decimal("0")
    bank_available_at_start = store.bank_totals()["bank_quantity_total"]

    for demand in demands:
        needed = _dec(demand["quantity"])
        need_qty_total += needed
        match_keys = _match_keys(
            demand.get("nomenclature_id"), demand.get("nomenclature_name")
        )
        left = needed
        from_warehouse = Decimal("0")
        from_supplier = Decimal("0")
        warehouse_parts: list[dict[str, Any]] = []
        supplier_parts: list[dict[str, Any]] = []
        seen_stock: set[str] = set()

        for nom_key in match_keys:
            for stock_id in stock_by_nom.get(nom_key, []):
                if left <= 0:
                    break
                if stock_id in seen_stock:
                    continue
                seen_stock.add(stock_id)
                available = remaining_stock.get(stock_id, Decimal("0"))
                if available <= 0:
                    continue
                take = min(left, available)
                remaining_stock[stock_id] = available - take
                from_warehouse += take
                left -= take
                meta = stock_meta[stock_id]
                wh_id = str(meta.get("warehouse_id") or "")
                warehouse_parts.append(
                    {
                        "stock_id": stock_id,
                        "warehouse_id": wh_id,
                        "warehouse_name": (warehouses.get(wh_id) or {}).get("name"),
                        "quantity": take,
                    }
                )
            if left <= 0:
                break

        supplier_candidates: list[str] = []
        seen_suppliers: set[str] = set()
        for nom_key in match_keys:
            for supplier_id in supplier_by_nom.get(nom_key, []):
                if supplier_id not in seen_suppliers:
                    seen_suppliers.add(supplier_id)
                    supplier_candidates.append(supplier_id)

        for supplier_id in supplier_candidates:
            if left <= 0:
                break
            take = Decimal("0")
            for offering in supplier_meta.get(supplier_id, {}).get("offerings") or []:
                if left <= 0:
                    break
                if not isinstance(offering, dict):
                    continue
                offering_keys = _match_keys(
                    str(offering.get("nomenclature_id") or "").strip(),
                    offering.get("nomenclature_name"),
                )
                if not any(key in match_keys for key in offering_keys):
                    continue
                oid = str(offering.get("nomenclature_id") or "").strip().casefold()
                if not oid:
                    continue
                key = (supplier_id, oid)
                available = remaining_supplier.get(key, Decimal("0"))
                if available <= 0:
                    continue
                part = min(left, available)
                remaining_supplier[key] = available - part
                take += part
                left -= part
            if take <= 0:
                continue
            from_supplier += take
            # Prefer a positive unit_price from a matching offering (for line totals).
            part_price = None
            for offering in supplier_meta.get(supplier_id, {}).get("offerings") or []:
                if not isinstance(offering, dict):
                    continue
                offering_keys = _match_keys(
                    str(offering.get("nomenclature_id") or "").strip(),
                    offering.get("nomenclature_name"),
                )
                if not any(key in match_keys for key in offering_keys):
                    continue
                try:
                    price = Decimal(str(offering.get("unit_price")))
                except Exception:
                    continue
                if price > 0:
                    part_price = price
                    break
            part_payload: dict[str, Any] = {
                "supplier_id": supplier_id,
                "supplier_name": (supplier_meta.get(supplier_id) or {}).get("name"),
                "quantity": take,
            }
            if part_price is not None:
                part_payload["unit_price"] = part_price
            supplier_parts.append(part_payload)

        covered = from_warehouse + from_supplier
        covered_qty_total += covered
        deficit = max(Decimal("0"), needed - covered)
        source = _source_kind(from_warehouse, from_supplier)
        tone = _line_tone(needed, covered)
        required = demand.get("required_date")
        if isinstance(required, datetime):
            required_out = required.isoformat()
        else:
            required_out = required

        line_payload = {
            "case_id": demand["case_id"],
            "line_id": demand["line_id"],
            "nomenclature_id": demand.get("nomenclature_id"),
            "nomenclature_name": demand.get("nomenclature_name"),
            "unit": demand.get("unit") or "шт",
            "required_date": required_out,
            "needed_quantity": needed,
            "covered_quantity": covered,
            "deficit_quantity": deficit,
            "from_warehouse": from_warehouse,
            "from_supplier": from_supplier,
            "coverage_source": source,
            "coverage_source_label": {
                "warehouse": "склад",
                "supplier": "поставщик",
                "mixed": "смешанный",
                "none": "нет",
            }[source],
            "tone": tone,
            "label": TONE_LABELS[tone],
            "warehouse_parts": [
                {**part, "quantity": str(part["quantity"])} for part in warehouse_parts
            ],
            "supplier_parts": [
                {
                    **part,
                    "quantity": str(part["quantity"]),
                    **(
                        {"unit_price": str(part["unit_price"])}
                        if part.get("unit_price") is not None
                        else {}
                    ),
                }
                for part in supplier_parts
            ],
        }
        # Keep Decimal in internal calc; serialize below.
        line_results.append(line_payload)
        case_lines.setdefault(demand["case_id"], []).append(line_payload)

    cases_out: list[dict[str, Any]] = []
    uncovered_orders = 0
    uncovered_positions = 0
    ready_orders = 0
    attention_orders = 0

    for case_id, lines in case_lines.items():
        tones = [line["tone"] for line in lines]
        tone = _case_tone(tones)
        # Count lines with any coverage (full or partial), not only fully ready.
        covered_count = sum(1 for line in lines if line["tone"] != "uncovered")
        positions_count = len(lines)
        uncovered_line_count = sum(1 for line in lines if line["tone"] == "uncovered")
        uncovered_positions += uncovered_line_count
        if tone == "uncovered":
            uncovered_orders += 1
        elif tone == "ready":
            ready_orders += 1
        else:
            attention_orders += 1
        cases_out.append(
            {
                "case_id": case_id,
                "tone": tone,
                "label": TONE_LABELS[tone],
                "covered_count": covered_count,
                "positions_count": positions_count,
                "uncovered_positions_count": uncovered_line_count,
                "needed_quantity": str(sum((line["needed_quantity"] for line in lines), Decimal("0"))),
                "covered_quantity": str(
                    sum((line["covered_quantity"] for line in lines), Decimal("0"))
                ),
                "deficit_quantity": str(
                    sum((line["deficit_quantity"] for line in lines), Decimal("0"))
                ),
                "lines": [_serialize_line(line) for line in lines],
            }
        )

    # Cases with zero demand lines still count as uncovered when they appear in input.
    seen = {item["case_id"] for item in cases_out}
    for case in cases:
        if isinstance(case, dict):
            case_id = str(case.get("id") or case.get("case_id") or "")
        else:
            case_id = str(getattr(case, "id", "") or "")
        if not case_id or case_id in seen:
            continue
        uncovered_orders += 1
        cases_out.append(
            {
                "case_id": case_id,
                "tone": "uncovered",
                "label": TONE_LABELS["uncovered"],
                "covered_count": 0,
                "positions_count": 0,
                "uncovered_positions_count": 0,
                "needed_quantity": "0",
                "covered_quantity": "0",
                "deficit_quantity": "0",
                "lines": [],
            }
        )

    lines_serialized = [_serialize_line(line) for line in line_results]
    price_bounds = supplier_price_bounds(store)
    by_nomenclature = _aggregate_positions(
        lines_serialized,
        price_bounds=price_bounds,
        bank=store,
    )

    return {
        "cases": cases_out,
        "lines": lines_serialized,
        "by_nomenclature": by_nomenclature,
        "price_formula": AMOUNT_FORMULA,
        "summary": {
            "total_orders_count": len(cases_out),
            "uncovered_orders_count": uncovered_orders,
            "ready_orders_count": ready_orders,
            "attention_orders_count": attention_orders,
            "uncovered_positions_count": uncovered_positions,
            "positions_count": len(lines_serialized),
            "need_quantity_total": str(need_qty_total),
            "covered_quantity_total": str(covered_qty_total),
            "bank_quantity_total": str(bank_available_at_start),
            "active_suppliers_count": len(store.active_suppliers()),
            "warehouses_count": len(store.warehouses()),
        },
        "case_index": {
            str(item["case_id"]).strip().casefold(): item for item in cases_out
        },
    }


def _serialize_line(line: dict[str, Any]) -> dict[str, Any]:
    return {
        **line,
        "needed_quantity": str(line["needed_quantity"]),
        "covered_quantity": str(line["covered_quantity"]),
        "deficit_quantity": str(line["deficit_quantity"]),
        "from_warehouse": str(line["from_warehouse"]),
        "from_supplier": str(line["from_supplier"]),
    }


def _merge_supplier_parts(
    target: dict[str, dict[str, Any]],
    parts: list[dict[str, Any]] | None,
) -> None:
    """Accumulate allocation supplier_parts by supplier_id (qty sum)."""
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        sid = str(part.get("supplier_id") or "").strip()
        if not sid:
            continue
        qty = _dec(part.get("quantity") or 0)
        if qty <= 0:
            continue
        existing = target.get(sid)
        if existing is None:
            row: dict[str, Any] = {
                "supplier_id": sid,
                "supplier_name": str(part.get("supplier_name") or sid),
                "quantity": qty,
            }
            if part.get("unit_price") is not None:
                row["unit_price"] = part.get("unit_price")
            target[sid] = row
            continue
        existing["quantity"] += qty
        if not existing.get("supplier_name") and part.get("supplier_name"):
            existing["supplier_name"] = str(part["supplier_name"])
        if existing.get("unit_price") is None and part.get("unit_price") is not None:
            existing["unit_price"] = part.get("unit_price")


def _aggregate_positions(
    lines: list[dict[str, Any]],
    *,
    price_bounds: dict[str, dict[str, Any]] | None = None,
    bank: MaterialBankStore | None = None,
) -> list[dict[str, Any]]:
    bounds = price_bounds or {}
    store = bank or get_material_bank()
    buckets: dict[str, dict[str, Any]] = {}
    for line in lines:
        key = _norm_key(line.get("nomenclature_id"), line.get("nomenclature_name")) or line[
            "line_id"
        ]
        bucket = buckets.get(key)
        if bucket is None:
            supplier_map: dict[str, dict[str, Any]] = {}
            _merge_supplier_parts(supplier_map, line.get("supplier_parts"))
            buckets[key] = {
                "nomenclature_id": line.get("nomenclature_id"),
                "nomenclature_name": line.get("nomenclature_name"),
                "unit": line.get("unit") or "шт",
                "needed_quantity": _dec(line["needed_quantity"]),
                "covered_quantity": _dec(line["covered_quantity"]),
                "deficit_quantity": _dec(line["deficit_quantity"]),
                "from_warehouse": _dec(line["from_warehouse"]),
                "from_supplier": _dec(line["from_supplier"]),
                "positions_count": 1,
                "sources": {line["coverage_source"]},
                "supplier_parts_map": supplier_map,
            }
            continue
        bucket["needed_quantity"] += _dec(line["needed_quantity"])
        bucket["covered_quantity"] += _dec(line["covered_quantity"])
        bucket["deficit_quantity"] += _dec(line["deficit_quantity"])
        bucket["from_warehouse"] += _dec(line["from_warehouse"])
        bucket["from_supplier"] += _dec(line["from_supplier"])
        bucket["positions_count"] += 1
        bucket["sources"].add(line["coverage_source"])
        _merge_supplier_parts(bucket["supplier_parts_map"], line.get("supplier_parts"))

    rows = []
    for key, bucket in buckets.items():
        sources = bucket.pop("sources")
        supplier_map = bucket.pop("supplier_parts_map", {}) or {}
        sources.discard("none")
        if len(sources) == 0:
            source: CoverageSource = "none"
        elif len(sources) == 1:
            source = next(iter(sources))  # type: ignore[assignment]
        else:
            source = "mixed"
        # Warehouse-only coverage: no suppliers in the plan.
        if source == "warehouse" or bucket["from_supplier"] <= 0:
            used_parts: list[dict[str, Any]] = []
        else:
            used_parts = []
            for part in sorted(
                supplier_map.values(),
                key=lambda item: (-_dec(item["quantity"]), str(item["supplier_name"])),
            ):
                row = {
                    "supplier_id": part["supplier_id"],
                    "supplier_name": part["supplier_name"],
                    "quantity": str(part["quantity"]),
                }
                if part.get("unit_price") is not None:
                    row["unit_price"] = str(part["unit_price"])
                used_parts.append(row)
        bound = bounds.get(key) or bounds.get(str(bucket.get("nomenclature_id") or "").casefold())
        price_min = bound["price_min"] if bound else None
        price_max = bound["price_max"] if bound else None
        nom_id = str(bucket.get("nomenclature_id") or "").strip()
        offers = collect_supplier_offers(nom_id, bank=store) if nom_id else []
        estimate = estimate_nomenclature_amount(
            bucket["needed_quantity"],
            price_min=price_min,
            coverage_source=source,
            from_supplier=bucket["from_supplier"],
            offers=offers,
        )
        rows.append(
            {
                **bucket,
                "needed_quantity": str(bucket["needed_quantity"]),
                "covered_quantity": str(bucket["covered_quantity"]),
                "deficit_quantity": str(bucket["deficit_quantity"]),
                "from_warehouse": str(bucket["from_warehouse"]),
                "from_supplier": str(bucket["from_supplier"]),
                "coverage_source": source,
                "coverage_source_label": {
                    "warehouse": "склад",
                    "supplier": "поставщик",
                    "mixed": "смешанный",
                    "none": "нет",
                }[source],
                "supplier_parts": used_parts,
                "used_suppliers": used_parts,
                "price_min": str(price_min) if price_min is not None else None,
                "price_max": str(price_max) if price_max is not None else None,
                "avg_unit_price": (
                    str(estimate.avg_unit_price)
                    if estimate.avg_unit_price is not None
                    else None
                ),
                "estimated_amount": (
                    str(estimate.amount) if estimate.amount is not None else None
                ),
                "amount": str(estimate.amount) if estimate.amount is not None else None,
                "overpay": str(estimate.overpay) if estimate.overpay else "0.00",
                "amount_source": estimate.source,
                "amount_formula": AMOUNT_FORMULA,
            }
        )
    rows.sort(
        key=lambda item: str(item.get("nomenclature_name") or item.get("nomenclature_id") or "")
    )
    return rows


__all__ = [
    "TONE_LABELS",
    "allocate_materials_by_deadline",
    "demands_from_cases",
]
