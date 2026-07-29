"""Supplier price bounds and estimate amounts for nomenclature aggregates."""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from typing import Any, Literal, Mapping, NamedTuple, Sequence

from app.agents.procurement_manager_agent.material_bank import MaterialBankStore, get_material_bank

CoverageSource = Literal["warehouse", "supplier", "mixed", "none"]

# estimated_amount = greedy cover of billable (supplier) qty by cheapest offers.
# Warehouse coverage is free for the estimate (amount = 0).
# avg_unit_price = estimated_amount / need_qty (кол-во потребности по строке).
# overpay = стоимость избытка при закупке лотом / min_order сверх потребности.
AMOUNT_FORMULA = (
    "Сумма = стоимость дозакупки у поставщиков: склад → 0; "
    "закупаемое кол-во закрывается жадно по возрастанию unit_price "
    "(берём покрытие остатка с учётом lot_size / min_order_qty / pack_qty, "
    "иначе min(остаток, available_qty) × unit_price). "
    "Если предложений не хватает — в сумму входит только покрытая часть. "
    "Средняя цена за ед. = сумма_по_номенклатуре / количество_потребности (need qty). "
    "Переплата (overpay) = стоимость избытка сверх need "
    "(когда лот/мин. заказ вынуждает купить больше остатка потребности); "
    "показывается отдельно, при этом входит в сумму. "
    "Override менеджера имеет приоритет над сметой банка. "
    "В колонке «Цена» — диапазон price_min – price_max среди поставщиков "
    "(справочный; сумма строится по покрывающим офферам, не как qty × price_min)."
)


class CoverResult(NamedTuple):
    amount: Decimal | None
    covered_qty: Decimal
    source: str
    overpay: Decimal
    purchased_qty: Decimal


class EstimateResult(NamedTuple):
    amount: Decimal | None
    source: str
    avg_unit_price: Decimal | None
    overpay: Decimal


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _avg_unit_price(amount: Decimal | None, need_qty: Decimal) -> Decimal | None:
    """Средняя цена = сумма / количество потребности; при qty=0 — None."""
    if amount is None:
        return None
    qty = need_qty if need_qty > 0 else Decimal("0")
    if qty <= 0:
        return None
    return _money(amount / qty)


def _offer_available(offer: Mapping[str, Any]) -> Decimal:
    qty = _dec(offer.get("available_qty"))
    if qty is None:
        qty = _dec(offer.get("available_quantity"))
    if qty is None or qty < 0:
        return Decimal("0")
    return qty


def _offer_lot_size(offer: Mapping[str, Any]) -> Decimal:
    """Lot / pack step; 1 means no forced lot over-buy."""
    for key in ("lot_size", "pack_qty", "pack_size"):
        raw = _dec(offer.get(key))
        if raw is not None and raw > 1:
            return raw
    return Decimal("1")


def _offer_min_order(offer: Mapping[str, Any]) -> Decimal:
    """Minimum purchase qty (floor); not a lot multiple unless lot_size is set."""
    raw = _dec(offer.get("min_order_qty"))
    if raw is None or raw <= 0:
        return Decimal("0")
    return raw


def _ceil_to_lot(qty: Decimal, lot: Decimal) -> Decimal:
    if lot <= 1:
        return qty
    if qty <= 0:
        return Decimal("0")
    lots = (qty / lot).to_integral_value(rounding=ROUND_CEILING)
    return lots * lot


def _floor_to_lot(qty: Decimal, lot: Decimal) -> Decimal:
    if lot <= 1:
        return qty
    if qty <= 0:
        return Decimal("0")
    lots = (qty / lot).to_integral_value(rounding=ROUND_DOWN)
    return lots * lot


def supplier_price_bounds(
    bank: MaterialBankStore | None = None,
) -> dict[str, dict[str, Any]]:
    """Min/max unit_price per nomenclature_id across active supplier offerings."""
    store = bank or get_material_bank()
    buckets: dict[str, dict[str, Any]] = {}
    for supplier in store.active_suppliers():
        for offering in supplier.get("offerings") or []:
            if not isinstance(offering, dict):
                continue
            nom = str(offering.get("nomenclature_id") or "").strip()
            if not nom:
                continue
            price = _dec(offering.get("unit_price"))
            if price is None or price < 0:
                continue
            key = nom.casefold()
            bucket = buckets.get(key)
            if bucket is None:
                buckets[key] = {
                    "nomenclature_id": nom,
                    "nomenclature_name": offering.get("nomenclature_name"),
                    "price_min": price,
                    "price_max": price,
                    "offer_count": 1,
                    "supplier_ids": {str(supplier.get("supplier_id") or "")},
                }
                continue
            bucket["price_min"] = min(bucket["price_min"], price)
            bucket["price_max"] = max(bucket["price_max"], price)
            bucket["offer_count"] += 1
            bucket["supplier_ids"].add(str(supplier.get("supplier_id") or ""))
            if not bucket.get("nomenclature_name") and offering.get("nomenclature_name"):
                bucket["nomenclature_name"] = offering.get("nomenclature_name")

    out: dict[str, dict[str, Any]] = {}
    for key, bucket in buckets.items():
        suppliers = {sid for sid in bucket["supplier_ids"] if sid}
        out[key] = {
            "nomenclature_id": bucket["nomenclature_id"],
            "nomenclature_name": bucket.get("nomenclature_name"),
            "price_min": bucket["price_min"],
            "price_max": bucket["price_max"],
            "offer_count": bucket["offer_count"],
            "suppliers_count": len(suppliers),
        }
    return out


def estimate_line_amount(
    quantity: Decimal,
    *,
    price_min: Decimal | None,
    unit_price_override: Decimal | None = None,
) -> tuple[Decimal | None, str]:
    """
    Return (amount, price_source).

    Priority: manager override → supplier price_min → none.
    """
    qty = quantity if quantity > 0 else Decimal("0")
    if unit_price_override is not None:
        amount = _money(unit_price_override * qty)
        return amount, "вручную"
    if price_min is not None:
        amount = _money(price_min * qty)
        return amount, "price_min"
    return None, "—"


def greedy_cover_cost(
    quantity: Decimal,
    offers: Sequence[Mapping[str, Any]] | None,
) -> CoverResult:
    """
    Cost ``quantity`` by cheapest-first cover from supplier offers.

    Respects ``lot_size`` / ``pack_qty`` / ``min_order_qty`` when present:
    purchase is rounded up to the lot (and min_order), capped by available_qty
    in lot multiples. Excess beyond need is costed into ``amount`` and reported
    separately as ``overpay``.

    Without lot fields, purchase never exceeds need (overpay = 0).
    Only the need qty that can be covered is counted in ``covered_qty``;
    shortfall is omitted from the amount except for lot over-buy on used offers.
    """
    need = quantity if quantity > 0 else Decimal("0")
    zero = Decimal("0.00")
    if need <= 0:
        return CoverResult(zero, Decimal("0"), "покрывающие офферы", zero, Decimal("0"))
    if not offers:
        return CoverResult(None, Decimal("0"), "—", zero, Decimal("0"))

    ranked: list[tuple[Decimal, Decimal, Decimal, Decimal, str]] = []
    for offer in offers:
        price = _dec(offer.get("unit_price"))
        available = _offer_available(offer)
        if price is None or price < 0 or available <= 0:
            continue
        lot = _offer_lot_size(offer)
        min_order = _offer_min_order(offer)
        sid = str(offer.get("supplier_id") or "")
        ranked.append((price, available, lot, min_order, sid))
    ranked.sort(key=lambda row: (row[0], -row[1], row[4]))

    left = need
    total = Decimal("0")
    covered = Decimal("0")
    purchased = Decimal("0")
    overpay = Decimal("0")
    for price, available, lot, min_order, _sid in ranked:
        if left <= 0:
            break

        purchase = left
        if lot > 1:
            purchase = _ceil_to_lot(left, lot)
        if min_order > purchase:
            purchase = min_order

        if purchase > available:
            if lot > 1:
                purchase = _floor_to_lot(available, lot)
            else:
                purchase = available

        if min_order > 0 and purchase < min_order:
            continue
        if purchase <= 0:
            continue

        to_need = min(left, purchase)
        excess = purchase - to_need
        line_cost = purchase * price
        total += line_cost
        overpay += excess * price
        covered += to_need
        purchased += purchase
        left -= to_need

    if covered <= 0:
        return CoverResult(None, Decimal("0"), "—", zero, Decimal("0"))
    return CoverResult(
        _money(total),
        covered,
        "покрывающие офферы",
        _money(overpay),
        purchased,
    )


def _billable_quantity(
    quantity: Decimal,
    *,
    coverage_source: CoverageSource | str | None = None,
    from_supplier: Decimal | None = None,
) -> tuple[Decimal, str | None]:
    """
    Resolve qty that contributes to purchase estimate.

    Returns (billable_qty, forced_source_label|None).
    forced_source_label "склад" means amount is exactly 0.
    """
    source = (coverage_source or "").strip().casefold() or None
    if source == "warehouse":
        return Decimal("0"), "склад"
    if source == "mixed" and from_supplier is not None:
        sp = from_supplier if from_supplier > 0 else Decimal("0")
        return sp, None
    if source == "supplier" and from_supplier is not None:
        sp = from_supplier if from_supplier > 0 else Decimal("0")
        return sp, None
    qty = quantity if quantity > 0 else Decimal("0")
    return qty, None


def estimate_nomenclature_amount(
    quantity: Decimal,
    *,
    price_min: Decimal | None,
    line_overrides: list[tuple[Decimal, Decimal | None]] | None = None,
    coverage_source: CoverageSource | str | None = None,
    from_supplier: Decimal | None = None,
    offers: Sequence[Mapping[str, Any]] | None = None,
) -> EstimateResult:
    """
    Aggregate amount for a nomenclature.

    Warehouse coverage → 0 (stock is not purchased).
    Supplier / mixed billable qty is costed by greedy cheapest-first offers
    (fallback: billable × price_min when offers are missing).

    ``avg_unit_price`` = amount / need ``quantity`` (потребность по строке).
    ``overpay`` = cost of lot/min-order excess beyond billable need.
    """
    need_qty = quantity if quantity > 0 else Decimal("0")
    zero = Decimal("0.00")

    def _finish(amount: Decimal | None, source: str, overpay: Decimal = zero) -> EstimateResult:
        return EstimateResult(
            amount,
            source,
            _avg_unit_price(amount, need_qty),
            overpay if overpay > 0 else zero,
        )

    billable_qty, forced = _billable_quantity(
        quantity,
        coverage_source=coverage_source,
        from_supplier=from_supplier,
    )
    if forced == "склад":
        return _finish(zero, "склад", zero)

    source = (coverage_source or "").strip().casefold() or None
    if source == "mixed":
        # Do not apply full-line overrides to warehouse+supplier mix;
        # bill only supplier-covered qty via covering offers.
        cover = greedy_cover_cost(billable_qty, offers)
        if cover.amount is not None:
            return _finish(cover.amount, cover.source, cover.overpay)
        amount, label = estimate_line_amount(
            billable_qty, price_min=price_min, unit_price_override=None
        )
        return _finish(amount, label, zero)

    if line_overrides:
        total = Decimal("0")
        overpay_total = Decimal("0")
        any_priced = False
        sources: set[str] = set()
        unpriced_qty = Decimal("0")
        for qty, override in line_overrides:
            if override is not None:
                amount, src = estimate_line_amount(
                    qty, price_min=price_min, unit_price_override=override
                )
                if amount is not None:
                    total += amount
                    any_priced = True
                    sources.add(src)
            else:
                unpriced_qty += qty if qty > 0 else Decimal("0")
        if unpriced_qty > 0:
            # Scale bank cover to the unpriced share of billable need.
            cover_qty = unpriced_qty
            if billable_qty < quantity and quantity > 0:
                cover_qty = (unpriced_qty * billable_qty / quantity).quantize(
                    Decimal("0.000001")
                )
            cover = greedy_cover_cost(cover_qty, offers)
            if cover.amount is not None:
                total += cover.amount
                overpay_total += cover.overpay
                any_priced = True
                sources.add(cover.source)
            else:
                fallback, src = estimate_line_amount(
                    cover_qty, price_min=price_min, unit_price_override=None
                )
                if fallback is not None:
                    total += fallback
                    any_priced = True
                    sources.add(src)
        if not any_priced:
            return _finish(None, "—", zero)
        amount = _money(total)
        overpay = _money(overpay_total)
        if sources == {"вручную"}:
            return _finish(amount, "вручную", overpay)
        if sources == {"price_min"}:
            return _finish(amount, "price_min", overpay)
        if sources == {"покрывающие офферы"}:
            return _finish(amount, "покрывающие офферы", overpay)
        return _finish(amount, "смешанный", overpay)

    cover = greedy_cover_cost(billable_qty, offers)
    if cover.amount is not None:
        return _finish(cover.amount, cover.source, cover.overpay)
    amount, label = estimate_line_amount(
        billable_qty, price_min=price_min, unit_price_override=None
    )
    return _finish(amount, label, zero)


__all__ = [
    "AMOUNT_FORMULA",
    "CoverResult",
    "EstimateResult",
    "estimate_line_amount",
    "estimate_nomenclature_amount",
    "greedy_cover_cost",
    "supplier_price_bounds",
]
