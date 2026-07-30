"""Qty-aware coverage of material-order positions by supplier and transfer orders.

Rules:
- Match by nomenclature_id (+ characteristic_id when both sides have it).
- Sum quantities and cap by requested need.
- Conditional (indefinite) supplier orders are excluded from qty when at least one
  definite child supplier order exists in the resolved chain.
- Transfer and supplier coverage combine for remaining need.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

_EMPTY_GUIDS = frozenset(
    {
        "",
        "00000000-0000-0000-0000-000000000000",
    }
)

COVERAGE_ARCHIVE_AFTER_DAYS = 7


def _text(value: Any) -> str:
    return str(value or "").strip()


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _norm_ref(value: Any) -> str:
    return _text(value).replace("{", "").replace("}", "").lower()


def _characteristic(value: Any) -> str | None:
    text = _norm_ref(value)
    if not text or text in _EMPTY_GUIDS:
        return None
    return text


def _document_cancelled(document: dict[str, Any]) -> bool:
    if bool(
        document.get("cancelled")
        or document.get("canceled")
        or document.get("DeletionMark")
        or document.get("deletion_mark")
    ):
        return True
    status = _text(
        document.get("order_status")
        or document.get("status")
        or document.get("Статус")
        or document.get("State")
    ).casefold()
    return any(
        marker in status
        for marker in (
            "отмен",
            "аннулир",
            "cancelled",
            "canceled",
            "deleted",
        )
    )


def is_definite_supplier_order(order: dict[str, Any]) -> bool:
    if order.get("is_definite") is False:
        return False
    if order.get("is_definite") is True or order.get("definite") is True:
        return True
    for key in (
        "counterpartyRef",
        "counterparty_ref",
        "Контрагент_Key",
        "partnerRef",
        "partner_ref",
        "Партнер_Key",
    ):
        ref = _norm_ref(order.get(key))
        if ref and ref not in _EMPTY_GUIDS:
            return True
    return False


def _order_ref(order: dict[str, Any]) -> str:
    return _norm_ref(
        order.get("supplier_order_1c_ref")
        or order.get("transfer_order_1c_ref")
        or order.get("ref")
        or order.get("Ref_Key")
    )


def _parent_refs_from_chain(order: dict[str, Any]) -> set[str]:
    resolution = (
        order.get("basisResolution")
        if isinstance(order.get("basisResolution"), dict)
        else {}
    )
    chain = order.get("chain") or resolution.get("chain") or []
    refs: set[str] = set()
    if not isinstance(chain, list):
        return refs
    for node in chain:
        if not isinstance(node, dict):
            continue
        for key in ("supplierOrderRef", "supplier_order_ref", "ref"):
            ref = _norm_ref(node.get(key))
            if ref:
                refs.add(ref)
    return refs


def select_supplier_orders_for_qty(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exclude conditional parents when definite children exist."""
    by_ref = {
        _order_ref(order): order
        for order in orders
        if _order_ref(order) and not _document_cancelled(order)
    }
    definite_refs = {
        ref for ref, order in by_ref.items() if is_definite_supplier_order(order)
    }
    excluded_parents: set[str] = set()
    for ref in definite_refs:
        order = by_ref[ref]
        for parent_ref in _parent_refs_from_chain(order):
            if parent_ref == ref:
                continue
            parent = by_ref.get(parent_ref)
            if parent is not None and not is_definite_supplier_order(parent):
                excluded_parents.add(parent_ref)
    return [
        order
        for ref, order in by_ref.items()
        if ref not in excluded_parents
    ]


def _match_key(nomenclature_id: str, characteristic_id: str | None) -> tuple[str, str | None]:
    return (_norm_ref(nomenclature_id), _characteristic(characteristic_id))


def _lines_from_order(order: dict[str, Any]) -> list[dict[str, Any]]:
    lines = order.get("lines") if isinstance(order.get("lines"), list) else []
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(lines, start=1):
        if not isinstance(raw, dict):
            continue
        if bool(raw.get("cancelled") or raw.get("Отменено")):
            continue
        nomenclature_id = _norm_ref(
            raw.get("nomenclature_id")
            or raw.get("nomenclatureRef")
            or raw.get("Номенклатура_Key")
        )
        if not nomenclature_id:
            continue
        out.append(
            {
                "line_id": _text(
                    raw.get("line_id")
                    or raw.get("LineNumber")
                    or raw.get("line_number")
                    or raw.get("lineNumber")
                    or index
                ),
                "nomenclature_id": nomenclature_id,
                "characteristic_id": _characteristic(
                    raw.get("characteristic_id")
                    or raw.get("characteristicRef")
                    or raw.get("Характеристика_Key")
                ),
                "quantity": _decimal(
                    raw.get("quantity") or raw.get("Количество") or raw.get("КоличествоУпаковок")
                ),
            }
        )
    return out


def _allocate_docs(
    *,
    need_qty: Decimal,
    nomenclature_id: str,
    characteristic_id: str | None,
    orders: list[dict[str, Any]],
    doc_kind: str,
    consumed: dict[tuple[str, str, str], Decimal],
) -> tuple[Decimal, list[dict[str, Any]]]:
    remaining = need_qty
    used: list[dict[str, Any]] = []
    if remaining <= 0:
        return Decimal("0"), used

    for order in orders:
        if _document_cancelled(order):
            continue
        order_ref = _order_ref(order)
        order_number = _text(
            order.get("supplier_order_number")
            or order.get("transfer_order_number")
            or order.get("number")
            or order.get("Number")
            or order_ref
        )
        for line in _lines_from_order(order):
            if line["nomenclature_id"] != nomenclature_id:
                continue
            line_char = line["characteristic_id"]
            if characteristic_id and line_char and line_char != characteristic_id:
                continue
            allocation_key = (doc_kind, order_ref, line["line_id"])
            already_consumed = consumed.get(allocation_key, Decimal("0"))
            available = max(Decimal("0"), line["quantity"] - already_consumed)
            if available <= 0:
                continue
            take = min(remaining, available)
            consumed[allocation_key] = already_consumed + take
            doc: dict[str, Any] = {
                "kind": doc_kind,
                "quantity": format(take.normalize(), "f"),
                "nomenclature_id": nomenclature_id,
                "characteristic_id": characteristic_id or line_char,
            }
            if doc_kind == "supplier_order":
                doc.update(
                    {
                        "supplier_order_1c_ref": order_ref,
                        "supplier_order_number": order_number,
                        "order_date": order.get("order_date")
                        or order.get("date")
                        or order.get("Date"),
                        "order_status": order.get("order_status") or order.get("status"),
                        "supplier_name": order.get("supplier_name")
                        or order.get("supplierName"),
                        "arrival_date": order.get("arrival_date")
                        or order.get("arrivalDate")
                        or order.get("desiredArrivalDate"),
                        "is_definite": is_definite_supplier_order(order),
                    }
                )
            else:
                doc.update(
                    {
                        "transfer_order_1c_ref": order_ref,
                        "transfer_order_number": order_number,
                        "order_date": order.get("order_date")
                        or order.get("date")
                        or order.get("Date"),
                        "order_status": order.get("order_status") or order.get("status"),
                        "warehouse_from_1c_ref": order.get("warehouse_from_1c_ref")
                        or order.get("warehouseFromRef")
                        or order.get("СкладОтправитель_Key"),
                        "warehouse_to_1c_ref": order.get("warehouse_to_1c_ref")
                        or order.get("warehouseToRef")
                        or order.get("СкладПолучатель_Key"),
                    }
                )
            used.append(doc)
            remaining -= take
            if remaining <= 0:
                return need_qty, used
    return need_qty - remaining, used


def coverage_source(
    *,
    supplier_qty: Decimal,
    transfer_qty: Decimal,
    requested: Decimal,
) -> str:
    covered = supplier_qty + transfer_qty
    if covered <= 0:
        return "none"
    if supplier_qty > 0 and transfer_qty > 0:
        return "mixed"
    if supplier_qty > 0:
        return "supplier_order"
    if transfer_qty > 0:
        return "transfer_order"
    return "none"


def build_material_order_coverage(
    *,
    positions: list[dict[str, Any]],
    supplier_orders: list[dict[str, Any]],
    transfer_orders: list[dict[str, Any]] | None = None,
    checked_at: str,
) -> dict[str, Any]:
    """Build unified coverage snapshot for active case positions."""
    qty_orders = select_supplier_orders_for_qty(supplier_orders)
    transfers = list(transfer_orders or [])
    position_rows: list[dict[str, Any]] = []
    covered_count = 0
    any_covered = False
    consumed: dict[tuple[str, str, str], Decimal] = {}

    for position in positions:
        if bool(position.get("cancelled")):
            continue
        nomenclature_id = _norm_ref(position.get("nomenclature_id"))
        if not nomenclature_id:
            continue
        characteristic_id = _characteristic(position.get("characteristic_id"))
        requested = _decimal(position.get("quantity") or position.get("requested_quantity"))
        # Internal transfer has priority: it represents stock already allocated
        # inside the company; supplier orders cover only the remaining need.
        transfer_qty, transfer_docs = _allocate_docs(
            need_qty=requested,
            nomenclature_id=nomenclature_id,
            characteristic_id=characteristic_id,
            orders=transfers,
            doc_kind="transfer_order",
            consumed=consumed,
        )
        supplier_qty, supplier_docs = _allocate_docs(
            need_qty=max(Decimal("0"), requested - transfer_qty),
            nomenclature_id=nomenclature_id,
            characteristic_id=characteristic_id,
            orders=qty_orders,
            doc_kind="supplier_order",
            consumed=consumed,
        )
        covered_qty = min(requested, supplier_qty + transfer_qty)
        remaining = max(Decimal("0"), requested - covered_qty)
        fully_covered = remaining <= 0 and requested > 0
        any_covered = any_covered or covered_qty > 0
        if fully_covered:
            covered_count += 1
        source = coverage_source(
            supplier_qty=supplier_qty,
            transfer_qty=transfer_qty,
            requested=requested,
        )
        position_rows.append(
            {
                "line_id": _text(position.get("line_id")),
                "nomenclature_id": nomenclature_id,
                "nomenclature_name": position.get("nomenclature_name"),
                "characteristic_id": characteristic_id,
                "requested_quantity": format(requested.normalize(), "f"),
                "supplier_ordered_quantity": format(supplier_qty.normalize(), "f"),
                "transfer_ordered_quantity": format(transfer_qty.normalize(), "f"),
                "covered_quantity": format(covered_qty.normalize(), "f"),
                "remaining_quantity": format(remaining.normalize(), "f"),
                "coverage_source": source,
                "purchasing": supplier_qty > 0,
                "transferring": transfer_qty > 0,
                "is_reconciled": covered_qty > 0,
                "fully_covered": fully_covered,
                # Backward-compatible aliases used by existing UI/merge code.
                "ordered_quantity": format(supplier_qty.normalize(), "f"),
                "supplier_orders": [
                    {
                        "supplier_order_1c_ref": doc.get("supplier_order_1c_ref"),
                        "supplier_order_number": doc.get("supplier_order_number"),
                        "order_date": doc.get("order_date"),
                        "order_status": doc.get("order_status"),
                        "supplier_name": doc.get("supplier_name"),
                        "arrival_date": doc.get("arrival_date"),
                        "quantity": doc.get("quantity"),
                        "is_definite": doc.get("is_definite"),
                    }
                    for doc in supplier_docs
                ],
                "transfer_orders": [
                    {
                        "transfer_order_1c_ref": doc.get("transfer_order_1c_ref"),
                        "transfer_order_number": doc.get("transfer_order_number"),
                        "order_date": doc.get("order_date"),
                        "order_status": doc.get("order_status"),
                        "warehouse_from_1c_ref": doc.get("warehouse_from_1c_ref"),
                        "warehouse_to_1c_ref": doc.get("warehouse_to_1c_ref"),
                        "quantity": doc.get("quantity"),
                    }
                    for doc in transfer_docs
                ],
            }
        )

    total = len(position_rows)
    coverage_status = (
        "full"
        if total and covered_count == total
        else "partial"
        if any_covered
        else "none"
    )
    summary = (
        "Все позиции перекрыты заказами поставщику и/или заказами на перемещение."
        if coverage_status == "full"
        else "Часть позиций перекрыта связанными заказами поставщику или перемещениями."
        if coverage_status == "partial"
        else "Связанные заказы поставщику и перемещения не найдены."
    )
    return {
        "schema_version": "2.0",
        "coverage_status": coverage_status,
        "covered_positions": covered_count,
        "positions_count": total,
        "positions": position_rows,
        "supplier_orders": supplier_orders,
        "transfer_orders": transfers,
        "checked_at": checked_at,
        "calculated_at": checked_at,
        "summary": summary,
        "recommended_next_step": (
            "Контролировать исполнение заказов поставщику и перемещений."
            if coverage_status == "full"
            else "Создать заказы поставщику или перемещения для непокрытых позиций."
            if coverage_status == "partial"
            else "Продолжить обеспечение заказа материалов."
        ),
        "decision_kind": "none",
    }


__all__ = [
    "COVERAGE_ARCHIVE_AFTER_DAYS",
    "build_material_order_coverage",
    "is_definite_supplier_order",
    "select_supplier_orders_for_qty",
]
