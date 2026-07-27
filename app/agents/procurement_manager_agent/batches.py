"""Purchase batches (партии) derived from coverage allocation and PO drafts.

Meter-based units (м) are split into physical length pieces — each cut/pipe
is its own batch even for the same nomenclature and delivery date.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

_MONEY_Q = Decimal("0.01")
_METER_Q = Decimal("0.1")

# Units that represent continuous length → split into piece batches.
_METER_UNITS = frozenset(
    {
        "м",
        "m",
        "м.",
        "пм",
        "п.м",
        "п.м.",
        "м.п",
        "м.п.",
        "meter",
        "meters",
        "metre",
        "metres",
    }
)


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _is_meter_unit(unit: Any) -> bool:
    key = str(unit or "").strip().casefold().replace("ё", "е")
    return key in _METER_UNITS


def _quantize_m(value: Decimal) -> Decimal:
    return value.quantize(_METER_Q, rounding=ROUND_HALF_UP)


def split_meter_pieces(
    total: Decimal | float | int | str,
    *,
    seed: int = 0,
    explicit: list[Any] | None = None,
) -> list[Decimal]:
    """Split total meters into physical piece lengths (sum == total).

    Prefer ``explicit`` lengths when provided and they sum to total (within 0.05).
    Otherwise generate 2+ pieces with varied lengths (e.g. 5.1 + 6.3).
    """
    need = _dec(total)
    if need <= 0:
        return []
    if explicit:
        pieces = [_quantize_m(_dec(item)) for item in explicit if _dec(item) > 0]
        if pieces:
            total_explicit = sum(pieces, Decimal("0"))
            if abs(total_explicit - need) <= Decimal("0.05"):
                # Adjust last piece to exact total.
                head = pieces[:-1]
                last = _quantize_m(need - sum(head, Decimal("0")))
                if last > 0:
                    return [*head, last]
                return pieces
            if abs(total_explicit - need) > Decimal("0.05") and total_explicit < need:
                rest = _quantize_m(need - total_explicit)
                if rest > 0:
                    return [*pieces, rest]

    # Single short piece — keep as one batch.
    if need <= Decimal("3"):
        return [_quantize_m(need)]

    # Generate varied pipe/cut lengths in ~4.0–7.5 m until remainder.
    bases = (
        Decimal("5.1"),
        Decimal("6.3"),
        Decimal("4.8"),
        Decimal("7.2"),
        Decimal("5.6"),
        Decimal("6.0"),
        Decimal("4.5"),
        Decimal("6.8"),
    )
    pieces: list[Decimal] = []
    remaining = need
    idx = abs(int(seed)) % len(bases)
    safety = 0
    while remaining > Decimal("3") and safety < 40:
        safety += 1
        piece = bases[idx % len(bases)]
        idx += 1
        # Slight deterministic jitter ±0.2 so pieces are not identical.
        jitter = Decimal("0.1") * Decimal((idx % 5) - 2)
        candidate = _quantize_m(piece + jitter)
        if candidate < Decimal("3"):
            candidate = Decimal("4.5")
        if candidate >= remaining:
            break
        # Leave a meaningful last piece (≥ 2.0 м) when possible.
        if remaining - candidate < Decimal("2"):
            break
        pieces.append(candidate)
        remaining = _quantize_m(remaining - candidate)

    if remaining > 0:
        pieces.append(_quantize_m(remaining))
    if not pieces:
        return [_quantize_m(need)]
    # Ensure exact sum.
    drift = _quantize_m(need - sum(pieces, Decimal("0")))
    if drift != 0:
        pieces[-1] = _quantize_m(pieces[-1] + drift)
    return [p for p in pieces if p > 0]


def _po_lines_by_line_id(workspace: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for item in workspace.get("purchase_order_drafts") or []:
        draft = item.get("draft") if isinstance(item, dict) and "draft" in item else item
        if not isinstance(draft, dict):
            continue
        supplier_id = draft.get("supplier_id")
        supplier_name = draft.get("supplier_name") or draft.get("supplier")
        for line in draft.get("lines") or []:
            if not isinstance(line, dict):
                continue
            line_id = str(line.get("line_id") or "").strip()
            if not line_id:
                continue
            row = dict(line)
            row.setdefault("supplier_id", supplier_id)
            row.setdefault("supplier_name", supplier_name)
            out.setdefault(line_id, []).append(row)
    return out


def _explicit_meter_pieces(
    *,
    line_id: str,
    position: Any,
    workspace: dict[str, Any],
) -> list[Any] | None:
    stored = workspace.get("meter_pieces")
    if isinstance(stored, dict) and stored.get(line_id):
        return list(stored[line_id])
    raw = getattr(position, "raw_payload", None)
    if isinstance(raw, dict) and raw.get("meter_pieces"):
        return list(raw["meter_pieces"])
    if isinstance(position, dict):
        if position.get("meter_pieces"):
            return list(position["meter_pieces"])
        raw = position.get("raw_payload")
        if isinstance(raw, dict) and raw.get("meter_pieces"):
            return list(raw["meter_pieces"])
    return None


def _expand_qty_to_batch_qtys(
    *,
    qty: Decimal,
    unit: Any,
    line_id: str,
    position: Any,
    workspace: dict[str, Any],
    seed: int,
) -> list[Decimal]:
    if not _is_meter_unit(unit):
        return [qty] if qty > 0 else []
    explicit = _explicit_meter_pieces(
        line_id=line_id, position=position, workspace=workspace
    )
    return split_meter_pieces(qty, seed=seed, explicit=explicit)


def build_batches_from_coverage(
    *,
    positions: list[Any],
    coverage_lines: list[dict[str, Any]] | None,
    workspace: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build sequential batches per receipt source (warehouse / supplier).

    For meter units each physical length is a separate batch.
    """
    ws = workspace or {}
    schedules = dict(ws.get("line_schedules") or {})
    po_by_line = _po_lines_by_line_id(ws)
    cov_by_line = {
        str(line.get("line_id") or ""): line
        for line in (coverage_lines or [])
        if isinstance(line, dict) and line.get("line_id")
    }
    batches: list[dict[str, Any]] = []
    batch_no = 1
    meter_pieces_out: dict[str, list[float]] = dict(ws.get("meter_pieces") or {})

    def _append_batch(
        *,
        line_id: str,
        quantity: Decimal,
        required_s: str | None,
        schedule: dict[str, Any],
        coverage_source: str,
        supplier_id: Any = None,
        supplier_name: Any = None,
        unit_price: Any = None,
        unit: str | None = None,
        piece_index: int | None = None,
        pieces_count: int | None = None,
    ) -> None:
        nonlocal batch_no
        row: dict[str, Any] = {
            "batch_no": batch_no,
            "line_id": line_id,
            "quantity": float(quantity),
            "required_date": schedule.get("required_date") or required_s,
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "coverage_source": coverage_source,
            "planned_arrival": schedule.get("planned_arrival"),
            "supplier_lead_days": schedule.get("supplier_lead_days"),
            "supplier_ship_date": schedule.get("supplier_ship_date"),
            "meets_deadline": schedule.get("meets_deadline"),
            "unit": unit,
        }
        if unit_price is not None:
            row["unit_price"] = float(unit_price)
        if piece_index is not None:
            row["piece_index"] = piece_index
            row["piece_label"] = f"отрезок {piece_index}"
        if pieces_count is not None and pieces_count > 1:
            row["is_meter_piece"] = True
        batches.append(row)
        batch_no += 1

    for pos_idx, position in enumerate(positions):
        if getattr(position, "cancelled", False):
            continue
        line_id = str(getattr(position, "line_id", None) or getattr(position, "id", "") or "")
        if not line_id:
            continue
        unit = getattr(position, "unit", None) or (
            position.get("unit") if isinstance(position, dict) else None
        )
        need = _dec(getattr(position, "quantity", 0))
        required = getattr(position, "required_date", None)
        required_s = required.isoformat() if hasattr(required, "isoformat") else (
            str(required)[:10] if required else None
        )
        cov = cov_by_line.get(line_id) or {}
        from_wh = _dec(cov.get("from_warehouse") or cov.get("warehouse_qty") or 0)
        from_sup = _dec(cov.get("from_supplier") or cov.get("supplier_qty") or 0)
        source = str(cov.get("coverage_source") or "none")
        used_suppliers = list(cov.get("used_suppliers") or [])
        schedule = schedules.get(line_id) if isinstance(schedules.get(line_id), dict) else {}
        seed = abs(hash(line_id)) + pos_idx * 17

        def _emit_pieces(
            qty: Decimal,
            *,
            coverage_source: str,
            supplier_id: Any = None,
            supplier_name: Any = None,
            unit_price: Any = None,
        ) -> None:
            pieces = _expand_qty_to_batch_qtys(
                qty=qty,
                unit=unit,
                line_id=line_id,
                position=position,
                workspace=ws,
                seed=seed,
            )
            if _is_meter_unit(unit) and pieces:
                meter_pieces_out[line_id] = [float(p) for p in pieces]
            for idx, piece_qty in enumerate(pieces, start=1):
                _append_batch(
                    line_id=line_id,
                    quantity=piece_qty,
                    required_s=required_s,
                    schedule=schedule,
                    coverage_source=coverage_source,
                    supplier_id=supplier_id,
                    supplier_name=supplier_name,
                    unit_price=unit_price,
                    unit=str(unit) if unit else None,
                    piece_index=idx if _is_meter_unit(unit) and len(pieces) > 1 else None,
                    pieces_count=len(pieces) if _is_meter_unit(unit) else None,
                )

        if from_wh > 0:
            _emit_pieces(
                from_wh,
                coverage_source="warehouse",
                supplier_name="Склад",
            )

        po_lines = po_by_line.get(line_id) or []
        if po_lines:
            for po_line in po_lines:
                qty = _dec(po_line.get("quantity") or from_sup or need)
                if qty <= 0:
                    continue
                _emit_pieces(
                    qty,
                    coverage_source="supplier",
                    supplier_id=po_line.get("supplier_id"),
                    supplier_name=po_line.get("supplier_name"),
                    unit_price=po_line.get("unit_price"),
                )
        elif from_sup > 0 or source in {"supplier", "mixed"}:
            supplier_name = None
            supplier_id = None
            if used_suppliers:
                first = used_suppliers[0]
                if isinstance(first, dict):
                    supplier_id = first.get("supplier_id") or first.get("id")
                    supplier_name = first.get("name") or first.get("supplier_name")
                else:
                    supplier_name = str(first)
            qty = from_sup if from_sup > 0 else need
            # Batch-level source is warehouse|supplier|none; line stays "mixed".
            _emit_pieces(
                qty,
                coverage_source="supplier",
                supplier_id=supplier_id,
                supplier_name=supplier_name,
            )
        elif from_wh <= 0:
            _emit_pieces(need, coverage_source="none")

    if meter_pieces_out:
        ws["meter_pieces"] = meter_pieces_out
    return batches


def sync_batches_workspace(
    workspace: dict[str, Any],
    *,
    positions: list[Any],
    coverage_lines: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    batches = build_batches_from_coverage(
        positions=positions,
        coverage_lines=coverage_lines or (workspace.get("order_coverage") or {}).get("lines"),
        workspace=workspace,
    )
    workspace["batches"] = batches
    return batches


__all__ = [
    "build_batches_from_coverage",
    "split_meter_pieces",
    "sync_batches_workspace",
]
