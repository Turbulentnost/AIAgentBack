"""Merge 1C orchestrator material-order coverage into PM order_coverage UI."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.material_order_coverage import select_supplier_orders_for_qty

_SOURCE_LABELS: dict[str, str] = {
    "warehouse": "склад",
    "supplier": "заказ поставщику",
    "supplier_order": "заказ поставщику",
    "transfer_order": "перемещение",
    "transfer": "перемещение",
    "mixed": "смешанный",
    "none": "нет",
}

_TONE_LABELS: dict[str, str] = {
    "ready": "Готов",
    "attention": "Требуют внимания",
    "uncovered": "Полностью необеспечен",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _norm_ref(value: Any) -> str:
    return _text(value).replace("{", "").replace("}", "").casefold()


def coverage_snapshot_from_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    meta = metadata or {}
    material = meta.get("material_order_coverage")
    if isinstance(material, dict):
        return material
    supplier = meta.get("supplier_order_coverage")
    if isinstance(supplier, dict):
        return supplier
    return {}


def definite_supplier_orders_from_snapshot(
    snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return non-conditional supplier orders for UI (exclude conditional parents)."""
    raw = (snapshot or {}).get("supplier_orders") or []
    orders = [item for item in raw if isinstance(item, dict)]
    selected = select_supplier_orders_for_qty(orders)
    out: list[dict[str, Any]] = []
    for order in selected:
        ref = _text(
            order.get("supplier_order_1c_ref")
            or order.get("ref")
            or order.get("Ref_Key")
        )
        number = _text(
            order.get("supplier_order_number")
            or order.get("number")
            or order.get("Number")
        )
        out.append(
            {
                "supplier_order_1c_ref": ref or None,
                "supplier_order_number": number or None,
                "order_date": order.get("order_date") or order.get("date"),
                "order_status": order.get("order_status") or order.get("status"),
                "supplier_name": order.get("supplier_name")
                or order.get("counterparty_name")
                or order.get("Контрагент"),
                "arrival_date": order.get("arrival_date"),
                "is_definite": order.get("is_definite", True),
            }
        )
    return out


def _map_orchestrator_source(source: str) -> str:
    value = _text(source).casefold()
    if value in {"supplier_order", "supplier"}:
        return "supplier_order"
    if value in {"transfer_order", "transfer"}:
        return "transfer_order"
    if value == "mixed":
        return "mixed"
    if value == "warehouse":
        return "warehouse"
    return value or "none"


def _position_index(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_line: dict[str, dict[str, Any]] = {}
    by_nom: dict[str, dict[str, Any]] = {}
    for position in snapshot.get("positions") or []:
        if not isinstance(position, dict):
            continue
        line_id = _text(position.get("line_id"))
        nom = _norm_ref(position.get("nomenclature_id") or position.get("nomenclature_ref"))
        if line_id:
            by_line[line_id.casefold()] = position
        if nom:
            by_nom[nom] = position
    return {"by_line": by_line, "by_nom": by_nom}


def _merge_line_with_orchestrator(
    line: dict[str, Any],
    orch: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(line)
    if not orch:
        return merged

    orch_source = _map_orchestrator_source(_text(orch.get("coverage_source")))
    bank_source = _text(merged.get("coverage_source")).casefold() or "none"
    supplier_qty = _dec(
        orch.get("supplier_ordered_quantity") or orch.get("ordered_quantity")
    )
    transfer_qty = _dec(orch.get("transfer_ordered_quantity"))
    covered_qty = _dec(orch.get("covered_quantity"))
    requested = _dec(orch.get("requested_quantity") or merged.get("needed_quantity"))
    if covered_qty <= 0:
        covered_qty = min(requested, supplier_qty + transfer_qty)

    from_warehouse = _dec(merged.get("from_warehouse"))
    # Prefer orchestrator purchase qty when bank has no supplier allocation.
    from_supplier = _dec(merged.get("from_supplier"))
    if supplier_qty > 0:
        from_supplier = max(from_supplier, supplier_qty)

    if orch_source in {"supplier_order", "transfer_order", "mixed"} and bank_source in {
        "none",
        "",
    }:
        source = orch_source
    elif orch_source == "transfer_order" and bank_source == "supplier":
        source = "mixed"
    elif orch_source == "supplier_order" and bank_source == "warehouse":
        source = "mixed"
    elif orch_source == "mixed":
        source = "mixed"
    elif orch_source in {"supplier_order", "transfer_order"}:
        source = orch_source if bank_source == "none" else (
            "mixed" if bank_source == "warehouse" else orch_source
        )
    else:
        source = bank_source if bank_source != "none" else orch_source or "none"

    if source in {"supplier_order", "supplier"} and from_supplier <= 0 and covered_qty > 0:
        from_supplier = covered_qty
    if source == "transfer_order" and covered_qty > 0 and from_supplier <= 0:
        # Treat transfer as covered without bank supplier offerings.
        from_supplier = Decimal("0")

    covered = max(_dec(merged.get("covered_quantity")), covered_qty, from_warehouse + from_supplier)
    if requested > 0:
        covered = min(requested, covered)
    deficit = max(Decimal("0"), requested - covered) if requested > 0 else _dec(
        merged.get("deficit_quantity")
    )

    if covered + Decimal("0.000001") >= requested > 0:
        tone = "ready"
    elif covered > 0:
        tone = "attention"
    else:
        tone = "uncovered"

    label = _SOURCE_LABELS.get(source, source or "нет")
    merged.update(
        {
            "from_warehouse": from_warehouse,
            "from_supplier": from_supplier,
            "covered_quantity": covered,
            "deficit_quantity": deficit,
            "needed_quantity": requested if requested > 0 else merged.get("needed_quantity"),
            "coverage_source": source,
            "coverage_source_label": label,
            "tone": tone,
            "label": _TONE_LABELS[tone],
            "purchasing": bool(orch.get("purchasing")) or supplier_qty > 0,
            "transferring": bool(orch.get("transferring")) or transfer_qty > 0,
            "supplier_orders": [
                item
                for item in (orch.get("supplier_orders") or [])
                if isinstance(item, dict)
            ],
            "transfer_orders": [
                item
                for item in (orch.get("transfer_orders") or [])
                if isinstance(item, dict)
            ],
        }
    )
    return merged


def merge_order_coverage_with_orchestrator(
    order_coverage: dict[str, Any] | None,
    *,
    metadata: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
    bucket_reason: str | None = None,
) -> dict[str, Any]:
    """Overlay 1C PO/transfer coverage onto bank allocation order_coverage."""
    snap = snapshot if isinstance(snapshot, dict) else coverage_snapshot_from_metadata(metadata)
    base = dict(order_coverage or {})
    lines_in = list(base.get("lines") or [])
    index = _position_index(snap)
    orch_positions = [
        item for item in (snap.get("positions") or []) if isinstance(item, dict)
    ]

    if not lines_in and orch_positions:
        lines_in = []
        for position in orch_positions:
            needed = _dec(position.get("requested_quantity") or position.get("quantity"))
            covered = _dec(position.get("covered_quantity"))
            source = _map_orchestrator_source(_text(position.get("coverage_source")))
            if covered + Decimal("0.000001") >= needed > 0:
                tone = "ready"
            elif covered > 0:
                tone = "attention"
            else:
                tone = "uncovered"
            lines_in.append(
                {
                    "line_id": _text(position.get("line_id")),
                    "nomenclature_id": position.get("nomenclature_id"),
                    "nomenclature_name": position.get("nomenclature_name"),
                    "needed_quantity": needed,
                    "covered_quantity": covered,
                    "deficit_quantity": _dec(position.get("remaining_quantity")),
                    "from_warehouse": Decimal("0"),
                    "from_supplier": _dec(position.get("supplier_ordered_quantity")),
                    "coverage_source": source,
                    "coverage_source_label": _SOURCE_LABELS.get(source, "нет"),
                    "tone": tone,
                    "label": _TONE_LABELS[tone],
                    "supplier_orders": position.get("supplier_orders") or [],
                    "transfer_orders": position.get("transfer_orders") or [],
                }
            )

    merged_lines: list[dict[str, Any]] = []
    seen_lines: set[str] = set()
    for line in lines_in:
        if not isinstance(line, dict):
            continue
        line_id = _text(line.get("line_id")).casefold()
        nom = _norm_ref(line.get("nomenclature_id"))
        orch = None
        if line_id and line_id in index["by_line"]:
            orch = index["by_line"][line_id]
        elif nom and nom in index["by_nom"]:
            orch = index["by_nom"][nom]
        merged = _merge_line_with_orchestrator(line, orch)
        merged_lines.append(merged)
        if line_id:
            seen_lines.add(line_id)

    for position in orch_positions:
        line_id = _text(position.get("line_id")).casefold()
        if line_id and line_id in seen_lines:
            continue
        merged_lines.append(_merge_line_with_orchestrator({}, position))

    covered_count = sum(1 for line in merged_lines if line.get("tone") == "ready")
    uncovered = sum(1 for line in merged_lines if line.get("tone") == "uncovered")
    positions_count = len(merged_lines)
    if positions_count == 0:
        tone = "uncovered"
    elif uncovered == positions_count:
        tone = "uncovered"
    elif covered_count == positions_count:
        tone = "ready"
    else:
        tone = "attention"

    orch_status = _text(snap.get("coverage_status")).casefold()
    has_suppliers = any(
        _dec(line.get("from_supplier")) > 0
        or _text(line.get("coverage_source"))
        in {"supplier", "supplier_order", "mixed"}
        for line in merged_lines
    )
    supplier_orders = definite_supplier_orders_from_snapshot(snap)

    label = _TONE_LABELS[tone]
    if bucket_reason:
        label = bucket_reason
    elif orch_status == "full":
        label = "Все позиции перекрыты заказами поставщику и/или перемещениями."
    elif orch_status == "partial":
        label = "Часть позиций ещё не покрыта закупками или перемещениями."

    return {
        **base,
        "tone": tone,
        "label": label,
        "covered_count": covered_count,
        "positions_count": positions_count or int(base.get("positions_count") or 0),
        "uncovered_positions_count": uncovered,
        "has_suppliers": has_suppliers or bool(supplier_orders),
        "lines": merged_lines,
        "supplier_orders": supplier_orders,
        "orchestrator_coverage_status": orch_status or None,
    }


def search_fields_from_coverage(
    *,
    source_number: str | None,
    order_coverage: dict[str, Any] | None,
    positions: list[Any] | None = None,
) -> dict[str, Any]:
    names: list[str] = []
    ids: list[str] = []
    for line in (order_coverage or {}).get("lines") or []:
        if not isinstance(line, dict):
            continue
        name = _text(line.get("nomenclature_name"))
        nom_id = _text(line.get("nomenclature_id"))
        if name and name not in names:
            names.append(name)
        if nom_id and nom_id not in ids:
            ids.append(nom_id)
    for position in positions or []:
        if isinstance(position, dict):
            name = _text(position.get("nomenclature_name"))
            nom_id = _text(position.get("nomenclature_id"))
        else:
            name = _text(getattr(position, "nomenclature_name", None))
            nom_id = _text(getattr(position, "nomenclature_id", None))
        if name and name not in names:
            names.append(name)
        if nom_id and nom_id not in ids:
            ids.append(nom_id)
    order_numbers = [
        _text(order.get("supplier_order_number"))
        for order in (order_coverage or {}).get("supplier_orders") or []
        if isinstance(order, dict) and _text(order.get("supplier_order_number"))
    ]
    return {
        "nomenclature_names": names,
        "nomenclature_ids": ids,
        "supplier_order_numbers": order_numbers,
        "search_text": " ".join(
            part
            for part in [
                _text(source_number),
                *names,
                *ids,
                *order_numbers,
            ]
            if part
        ).casefold(),
    }


__all__ = [
    "coverage_snapshot_from_metadata",
    "definite_supplier_orders_from_snapshot",
    "merge_order_coverage_with_orchestrator",
    "search_fields_from_coverage",
]
