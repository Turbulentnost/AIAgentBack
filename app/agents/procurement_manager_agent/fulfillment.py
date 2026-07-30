"""Order fulfillment lifecycle status for procurement manager UI."""

from __future__ import annotations

from typing import Any, Literal

FulfillmentStatus = Literal[
    "no_supplier",
    "payment",
    "delivery",
    "otk_presentation",
    "posting",
    "completed",
]

FULFILLMENT_LABELS: dict[str, str] = {
    "no_supplier": "Не выбран поставщик",
    "payment": "Оплата (в процессе)",
    "delivery": "Поставка",
    "otk_presentation": "Предъявление ОТК",
    "posting": "Оприходование",
    "completed": "Выполнен",
}

FULFILLMENT_TONES: dict[str, str] = {
    "no_supplier": "yellow_blink",
    "payment": "blue",
    "delivery": "blue",
    "otk_presentation": "yellow",
    "posting": "green",
    "completed": "muted",
}

_CASE_STATUS_MAP: dict[str, FulfillmentStatus] = {
    "payment_pending": "payment",
    "ordered": "delivery",
    "in_transit": "delivery",
    "receiving": "otk_presentation",
    "posting_required": "posting",
    "posted": "completed",
}


def _has_1c_supplier_orders(workspace: dict[str, Any] | None) -> bool:
    ws = workspace or {}
    orders = ws.get("supplier_orders") or (ws.get("order_coverage") or {}).get(
        "supplier_orders"
    )
    if isinstance(orders, list) and any(
        isinstance(item, dict)
        and (
            item.get("supplier_order_1c_ref")
            or item.get("supplier_order_number")
            or item.get("is_definite")
        )
        for item in orders
    ):
        return True
    coverage = ws.get("order_coverage") if isinstance(ws.get("order_coverage"), dict) else {}
    status = str(coverage.get("orchestrator_coverage_status") or "").casefold()
    if status in {"partial", "full"}:
        return True
    for line in coverage.get("lines") or []:
        if not isinstance(line, dict):
            continue
        source = str(line.get("coverage_source") or "").casefold()
        if source in {"supplier", "supplier_order", "mixed", "transfer_order"}:
            return True
        if line.get("supplier_orders"):
            return True
    return False


def _has_supplier_selection(workspace: dict[str, Any] | None) -> bool:
    ws = workspace or {}
    if _has_1c_supplier_orders(ws):
        return True
    drafts = ws.get("purchase_order_drafts") or []
    for item in drafts:
        draft = item.get("draft") if isinstance(item, dict) and "draft" in item else item
        if not isinstance(draft, dict):
            continue
        if draft.get("supplier_id") or draft.get("supplier_name"):
            return True
        lines = draft.get("lines") or []
        if any(
            isinstance(line, dict) and (line.get("supplier_id") or line.get("unit_price"))
            for line in lines
        ):
            return True
    rec = ws.get("recommendation") or {}
    if isinstance(rec, dict) and (rec.get("supplier_id") or rec.get("selected_supplier_id")):
        return True
    approved = [
        a
        for a in (ws.get("approvals") or [])
        if isinstance(a, dict)
        and a.get("operation") == "select_supplier"
        and a.get("status") == "approved"
    ]
    return bool(approved)


def derive_fulfillment_status(
    *,
    case_status: str | None,
    workspace: dict[str, Any] | None = None,
    manual: str | None = None,
) -> FulfillmentStatus:
    """Resolve fulfillment status: manual override → case status → supplier heuristic."""
    if manual and manual in FULFILLMENT_LABELS:
        return manual  # type: ignore[return-value]
    ws = workspace or {}
    stored = ws.get("fulfillment_status")
    if stored and str(stored) in FULFILLMENT_LABELS and ws.get("fulfillment_status_manual"):
        return str(stored)  # type: ignore[return-value]
    mapped = _CASE_STATUS_MAP.get((case_status or "").strip().casefold())
    if mapped:
        return mapped
    if stored and str(stored) in FULFILLMENT_LABELS:
        return str(stored)  # type: ignore[return-value]
    # Real 1C supplier orders mean purchasing already started → delivery.
    if _has_1c_supplier_orders(ws):
        lifecycle = str(ws.get("lifecycle_state") or "").casefold()
        if "payment" in lifecycle:
            return "payment"
        if lifecycle in {"received", "receiving"}:
            return "otk_presentation"
        if "post" in lifecycle:
            return "posting"
        return "delivery"
    if not _has_supplier_selection(ws):
        return "no_supplier"
    # PO/supplier exists but payment/delivery not started yet → still no_supplier until payment.
    lifecycle = str(ws.get("lifecycle_state") or "").casefold()
    if lifecycle in {
        "purchase_order_draft",
        "approval_required",
        "rfq_draft",
        "quotes_received",
    }:
        return "no_supplier"
    if "payment" in lifecycle:
        return "payment"
    if lifecycle in {"ordered", "dispatched", "in_transit", "delayed"}:
        return "delivery"
    if lifecycle in {"received", "receiving"}:
        return "otk_presentation"
    if "post" in lifecycle:
        return "posting"
    return "no_supplier" if not _has_supplier_selection(ws) else "payment"


def fulfillment_payload(
    *,
    case_status: str | None,
    workspace: dict[str, Any] | None = None,
    manual: str | None = None,
) -> dict[str, Any]:
    status = derive_fulfillment_status(
        case_status=case_status, workspace=workspace, manual=manual
    )
    return {
        "fulfillment_status": status,
        "fulfillment_label": FULFILLMENT_LABELS[status],
        "fulfillment_tone": FULFILLMENT_TONES[status],
        "show_otk_button": status == "otk_presentation",
        "is_completed": status == "completed",
    }


__all__ = [
    "FULFILLMENT_LABELS",
    "FULFILLMENT_TONES",
    "FulfillmentStatus",
    "derive_fulfillment_status",
    "fulfillment_payload",
]
