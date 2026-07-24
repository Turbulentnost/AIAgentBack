"""Procurement coverage optimizer: deadline → overpay/cost → delivery speed.

Priority gates (deterministic, not a flat weighted sum):

1. ``meets_deadline`` — поставщик успевает к ``required_date``
   (today + lead_time_days ≤ required_date); без срока — все равны.
2. Среди успевающих — минимизировать ``total_cost`` = coverage_cost + overpay
   (переплата за лот / min_order сверх потребности).
3. Тай-брейк: меньший ``lead_time_days``, затем меньший ``unit_price``.
4. Если никто не успевает — риск, выбираем лучшего из доступных с причиной.

Bank-first: склад (и остаток банка) закрывается через
``allocate_materials_by_deadline``; оптимизатор ранжирует поставщиков
на **остаток** после склада.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, Mapping, Sequence

from app.agents.procurement_manager_agent.material_bank import MaterialBankStore, get_material_bank
from app.agents.procurement_manager_agent.pricing import greedy_cover_cost

_SCORE_QUANT = Decimal("0.0001")
_MONEY_QUANT = Decimal("0.01")
_ONE = Decimal("1")
_ZERO = Decimal("0")
_MISSING_LEAD = 10**9

WaveMode = Literal["urgent", "economy"]

OPTIMIZATION_FORMULA = (
    "Приоритет (гейты, не взвешенная сумма): "
    "1) срок — meets_deadline: today + lead_time_days ≤ required_date "
    "(без required_date все равны; без lead_time — не подтверждён срок); "
    "2) цена с переплатой — minimize total_cost = coverage_cost + overpay "
    "(лот/min_order сверх потребности); "
    "3) скорость — меньший lead_time_days; "
    "4) меньший unit_price. "
    "Склад/банк сначала (allocate_materials_by_deadline по required_date FIFO); "
    "оптимизатор — на остаток у поставщиков. "
    "Без успевающих в срок — риск, лучший из доступных. "
    "Очередь: wave_mode=urgent — срок жёстче цены; "
    "wave_mode=economy — среди успевающих минимум цены, другие supplier_id допустимы."
)

QUEUE_OPTIMIZATION_FORMULA = (
    "optimize_queue_coverage: волны critical/medium → urgent (срок > цена); "
    "late → economy (цена среди успевающих, смена поставщика относительно urgent-волны). "
    "Склад FIFO залочен глобально; остаток поставщиков потребляется по волнам."
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


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _lead_days(offer: Mapping[str, Any]) -> int | None:
    raw = offer.get("lead_time_days")
    if raw is None or raw == "":
        return None
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return None
    return days if days >= 0 else None


def offer_meets_deadline(
    required_date: Any,
    lead_time_days: int | None,
    *,
    today: date | None = None,
) -> bool | None:
    """
    True if arrival (today + lead) is on/before required_date.

    None — cannot verify (no lead_time while deadline is set).
    True when no required_date (no deadline constraint).
    """
    req = _parse_date(required_date)
    if req is None:
        return True
    if lead_time_days is None:
        return None
    as_of = today or date.today()
    arrival = as_of + timedelta(days=int(lead_time_days))
    return arrival <= req


def _single_offer_cost(need: Decimal, offer: Mapping[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
    """Return (total_cost, overpay, coverable_qty) for one offer vs need."""
    cover = greedy_cover_cost(need, [offer])
    if cover.amount is None:
        return Decimal("0"), Decimal("0"), Decimal("0")
    return cover.amount, cover.overpay, cover.covered_qty


def _build_optimization_reason(
    *,
    meets: bool | None,
    overpay: Decimal,
    lead_time_days: int | None,
    unit_price: Decimal,
    min_price: Decimal,
    coverage_ratio: Decimal,
    deadline_risk: bool,
) -> str:
    parts: list[str] = []
    if meets is True:
        parts.append("срок ок")
    elif meets is False:
        parts.append("срок нет")
    elif meets is None and deadline_risk:
        parts.append("срок неизвестен")
    if overpay > 0:
        parts.append(f"переплата {_round_money(overpay)}")
    if lead_time_days is not None:
        parts.append(f"поставка {lead_time_days} дн")
    if unit_price == min_price:
        parts.append("дешевле")
    if coverage_ratio >= _ONE:
        parts.append("закрывает 100%")
    elif coverage_ratio > 0:
        pct = int((coverage_ratio * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP))
        parts.append(f"частично {pct}%")
    if deadline_risk and meets is not True:
        parts.append("риск срока")
    return ", ".join(parts) if parts else "кандидат"


def optimize_supplier_offers(
    need_qty: Decimal,
    offers: Sequence[Mapping[str, Any]],
    *,
    required_date: Any = None,
    today: date | None = None,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """
    Rank supplier offers by deadline → total cost (with overpay) → speed → price.

    Returns rows compatible with TopSupplierOffer (+ optimization fields).
    """
    need = need_qty if need_qty > 0 else _ZERO
    if need <= 0 or top_n <= 0 or not offers:
        return []

    as_of = today or date.today()
    has_deadline = _parse_date(required_date) is not None
    prices = [_dec(item.get("unit_price")) for item in offers]
    prices = [p for p in prices if p is not None and p >= 0]
    if not prices:
        return []
    min_price = min(prices)
    max_price = max(prices)
    price_span = max_price - min_price

    ranked: list[dict[str, Any]] = []
    any_meets = False
    for item in offers:
        unit_price = _dec(item.get("unit_price"))
        available = _dec(item.get("available_qty"))
        if available is None:
            available = _dec(item.get("available_quantity"))
        if unit_price is None or unit_price < 0 or available is None or available <= 0:
            continue
        lead = _lead_days(item)
        meets = offer_meets_deadline(required_date, lead, today=as_of)
        if meets is True:
            any_meets = True
        total_cost, overpay, coverable_from_cover = _single_offer_cost(need, item)
        coverable_qty = min(need, available)
        # Prefer cover qty from lot-aware cover when it purchased something.
        if coverable_from_cover > 0:
            coverable_qty = min(need, coverable_from_cover)
        coverage_ratio = (coverable_qty / need) if need > 0 else _ZERO
        if price_span == 0:
            price_score = _ONE
        else:
            price_score = (max_price - unit_price) / price_span
        coverage_score = coverage_ratio
        # Informational utility (does not override deadline gates in sort).
        informational = (
            Decimal("0.55") * price_score
            + Decimal("0.45") * coverage_score
            - Decimal("0.05") * (_ONE - coverage_ratio)
        )
        deadline_ok = meets is True
        ranked.append(
            {
                "rank": 0,
                "supplier_id": str(item.get("supplier_id") or ""),
                "supplier_name": str(
                    item.get("supplier_name") or item.get("supplier_id") or ""
                ),
                "nomenclature_id": item.get("nomenclature_id"),
                "nomenclature_name": item.get("nomenclature_name"),
                "unit_price": _round_money(unit_price),
                "available_qty": available,
                "coverable_qty": coverable_qty,
                "coverage_ratio": _round_score(coverage_ratio),
                "coverage_cost": _round_money(total_cost - overpay)
                if total_cost >= overpay
                else _round_money(coverable_qty * unit_price),
                "total_cost": _round_money(total_cost)
                if total_cost > 0
                else _round_money(coverable_qty * unit_price),
                "overpay": _round_money(overpay),
                "price_score": _round_score(price_score),
                "coverage_score": _round_score(coverage_score),
                "score": _round_score(informational),
                "meets_deadline": deadline_ok if has_deadline else True,
                "deadline_status": (
                    "ok"
                    if meets is True
                    else ("unknown" if meets is None else "miss")
                ),
                "lead_time_days": lead,
                "unit": item.get("unit") or "шт",
                "optimization_reason": "",
                "reason": "",
                "_sort_meets": 0 if deadline_ok else 1,
                "_sort_cover": -coverable_qty,  # more coverage first within cohort
                "_sort_cost": total_cost
                if total_cost > 0
                else (coverable_qty * unit_price),
                "_sort_lead": lead if lead is not None else _MISSING_LEAD,
                "_sort_price": unit_price,
                "_meets_raw": meets,
            }
        )

    if not ranked:
        return []

    deadline_risk = has_deadline and not any_meets
    ranked.sort(
        key=lambda row: (
            row["_sort_meets"],
            row["_sort_cover"],
            row["_sort_cost"],
            row["_sort_lead"],
            row["_sort_price"],
            row["supplier_id"],
        )
    )
    top = ranked[:top_n]
    n = len(top)
    for index, row in enumerate(top, start=1):
        meets_raw = row.pop("_meets_raw")
        row.pop("_sort_meets", None)
        row.pop("_sort_cover", None)
        row.pop("_sort_cost", None)
        row.pop("_sort_lead", None)
        row.pop("_sort_price", None)
        reason = _build_optimization_reason(
            meets=meets_raw,
            overpay=row["overpay"],
            lead_time_days=row["lead_time_days"],
            unit_price=row["unit_price"],
            min_price=min_price,
            coverage_ratio=row["coverage_ratio"],
            deadline_risk=deadline_risk,
        )
        row["rank"] = index
        row["optimization_rank"] = index
        row["optimization_reason"] = reason
        row["reason"] = reason
        row["deadline_risk"] = deadline_risk
        # Gate-aware display score in [0, 1]: deadline cohort [0.5..1], else [0..0.5].
        gate = Decimal("0.5") if row["meets_deadline"] else Decimal("0")
        within = (
            Decimal(str(n - index + 1)) / Decimal(str(n)) * Decimal("0.4999")
            if n
            else _ZERO
        )
        row["score"] = _round_score(gate + within)
    return top


def rank_offers_for_need(
    nomenclature_id: str,
    need_qty: Decimal,
    *,
    bank: MaterialBankStore | None = None,
    required_date: Any = None,
    today: date | None = None,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """Collect bank offers and optimize for one nomenclature need."""
    # Lazy import avoids cycle: supplier_ranking → optimize → collect_supplier_offers.
    from app.agents.procurement_manager_agent.supplier_ranking import collect_supplier_offers

    store = bank or get_material_bank()
    offers = collect_supplier_offers(nomenclature_id, bank=store)
    return optimize_supplier_offers(
        need_qty,
        offers,
        required_date=required_date,
        today=today,
        top_n=top_n,
    )


def optimize_case_coverage(
    positions: list[dict[str, Any]] | list[Any],
    *,
    bank: MaterialBankStore | None = None,
    offers: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    today: date | None = None,
    top_n: int = 3,
    case_id: str | None = None,
    case_required_date: Any = None,
) -> dict[str, Any]:
    """
    Bank-first coverage plan, then supplier optimization on warehouse remainder.

    ``offers`` optional map nomenclature_id → offer list; defaults to bank.
    """
    # Lazy imports avoid cycles with allocation / supplier_ranking.
    from app.agents.procurement_manager_agent.allocation import allocate_materials_by_deadline
    from app.agents.procurement_manager_agent.supplier_ranking import collect_supplier_offers

    store = bank or get_material_bank()
    case_stub = {
        "id": case_id or "optimize-case",
        "required_date": case_required_date,
        "positions": positions,
    }
    allocation = allocate_materials_by_deadline([case_stub], bank=store)
    lines_out: list[dict[str, Any]] = []
    picks: list[dict[str, Any]] = []

    for line in allocation.get("lines") or []:
        nom = str(line.get("nomenclature_id") or "").strip()
        needed = _dec(line.get("needed_quantity")) or _ZERO
        from_wh = _dec(line.get("from_warehouse")) or _ZERO
        remainder = needed - from_wh
        if remainder < 0:
            remainder = _ZERO
        required = line.get("required_date") or case_required_date
        offer_list: Sequence[Mapping[str, Any]]
        if offers and nom:
            offer_list = offers.get(nom) or offers.get(nom.casefold()) or []
        else:
            offer_list = collect_supplier_offers(nom, bank=store) if nom else []
        top = (
            optimize_supplier_offers(
                remainder,
                offer_list,
                required_date=required,
                today=today,
                top_n=top_n,
            )
            if remainder > 0 and offer_list
            else []
        )
        primary = top[0] if top else None
        row = {
            "line_id": line.get("line_id"),
            "case_id": line.get("case_id"),
            "nomenclature_id": nom or None,
            "nomenclature_name": line.get("nomenclature_name"),
            "needed_quantity": str(needed),
            "from_warehouse": str(from_wh),
            "supplier_remainder": str(remainder),
            "required_date": required,
            "coverage_source": line.get("coverage_source"),
            "tone": line.get("tone"),
            "top_suppliers": top,
            "recommended_supplier_id": primary["supplier_id"] if primary else None,
            "meets_deadline": primary["meets_deadline"] if primary else None,
            "overpay": str(primary["overpay"]) if primary else "0.00",
            "lead_time_days": primary.get("lead_time_days") if primary else None,
            "optimization_rank": primary.get("optimization_rank") if primary else None,
            "optimization_reason": primary.get("optimization_reason") if primary else None,
            "deadline_risk": bool(primary.get("deadline_risk")) if primary else False,
        }
        lines_out.append(row)
        if primary:
            picks.append(
                {
                    "line_id": row["line_id"],
                    "nomenclature_id": nom,
                    "supplier_id": primary["supplier_id"],
                    "supplier_name": primary["supplier_name"],
                    "meets_deadline": primary["meets_deadline"],
                    "total_cost": str(primary.get("total_cost")),
                    "overpay": str(primary["overpay"]),
                    "lead_time_days": primary.get("lead_time_days"),
                    "optimization_reason": primary.get("optimization_reason"),
                }
            )

    return {
        "allocation": allocation,
        "lines": lines_out,
        "picks": picks,
        "optimization_formula": OPTIMIZATION_FORMULA,
        "summary": {
            "lines_count": len(lines_out),
            "picks_count": len(picks),
            "deadline_risk_lines": sum(1 for line in lines_out if line.get("deadline_risk")),
            **(allocation.get("summary") or {}),
        },
    }


def _offer_available(offer: Mapping[str, Any]) -> Decimal:
    available = _dec(offer.get("available_qty"))
    if available is None:
        available = _dec(offer.get("available_quantity"))
    return available if available is not None else _ZERO


def _apply_remaining_qty(
    offers: Sequence[Mapping[str, Any]],
    remaining: dict[tuple[str, str], Decimal],
    nom_key: str,
) -> list[dict[str, Any]]:
    """Clone offers with qty clamped to residual supplier inventory."""
    out: list[dict[str, Any]] = []
    for offer in offers:
        sid = str(offer.get("supplier_id") or "").strip()
        if not sid:
            continue
        key = (sid, nom_key)
        left = remaining.get(key)
        if left is None:
            # Unknown key: use offer qty (first-seen seed).
            left = _offer_available(offer)
            remaining[key] = left
        if left <= 0:
            continue
        cloned = dict(offer)
        cloned["available_qty"] = left
        cloned["available_quantity"] = left
        out.append(cloned)
    return out


def _rank_for_wave_mode(
    need: Decimal,
    offers: Sequence[Mapping[str, Any]],
    *,
    required_date: Any,
    today: date | None,
    wave_mode: WaveMode,
    top_n: int,
) -> list[dict[str, Any]]:
    """Rank offers; economy prefers cost among deadline-feasible, then speed."""
    ranked = optimize_supplier_offers(
        need,
        offers,
        required_date=required_date,
        today=today,
        top_n=max(top_n, len(list(offers))),
    )
    if not ranked:
        return []
    if wave_mode != "economy":
        return ranked[:top_n]

    # Economy: among rows that meet deadline (or no deadline), minimize total_cost.
    feasible = [row for row in ranked if row.get("meets_deadline") is True]
    pool = feasible or ranked
    pool = sorted(
        pool,
        key=lambda row: (
            _dec(row.get("total_cost")) or Decimal("999999999"),
            row.get("lead_time_days")
            if row.get("lead_time_days") is not None
            else _MISSING_LEAD,
            _dec(row.get("unit_price")) or Decimal("999999999"),
            str(row.get("supplier_id") or ""),
        ),
    )
    top = pool[:top_n]
    for index, row in enumerate(top, start=1):
        row = dict(row)
        row["rank"] = index
        row["optimization_rank"] = index
        row["wave_mode"] = "economy"
        reason = row.get("optimization_reason") or row.get("reason") or ""
        if "economy" not in reason:
            row["optimization_reason"] = (
                f"economy: цена среди успевающих; {reason}".strip("; ")
            )
            row["reason"] = row["optimization_reason"]
        top[index - 1] = row
    return top


def _consume_pick(
    remaining: dict[tuple[str, str], Decimal],
    *,
    supplier_id: str,
    nom_key: str,
    qty: Decimal,
) -> None:
    key = (supplier_id, nom_key)
    left = remaining.get(key, _ZERO) - qty
    remaining[key] = left if left > 0 else _ZERO


def optimize_queue_coverage(
    cases: list[Any],
    *,
    bank: MaterialBankStore | None = None,
    offers_by_nom: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    today: date | None = None,
    top_n: int = 3,
    waves: Mapping[str, Any] | None = None,
    wave_mode: WaveMode | None = None,
) -> dict[str, Any]:
    """
    Cross-order coverage: global bank allocate, then per-wave supplier picks.

    Assignments: ``(case_id, line_id) → supplier_parts``.
    ``wave_mode`` override forces one mode for all waves; otherwise each wave
    uses its own mode (urgent for critical/medium, economy for late).

    Economy waves may pick different/cheaper suppliers than urgent primaries;
    ``supplier_diversity`` lists those alternatives.
    """
    from app.agents.procurement_manager_agent.supplier_ranking import collect_supplier_offers
    from app.agents.procurement_manager_agent.waves import (
        allocate_queue_with_waves,
        bucket_urgency_waves,
        wave_mode_for_label,
    )

    store = bank or get_material_bank()
    as_of = today or date.today()
    packed = allocate_queue_with_waves(cases, bank=store, today=as_of)
    wave_plan = dict(waves) if waves else dict(packed.get("waves") or {})
    if not wave_plan.get("waves"):
        wave_plan = bucket_urgency_waves(cases, today=as_of)

    # Supplier residual starts at full bank; warehouse is already FIFO-locked in allocate.
    # Waves consume supplier qty in order so late/economy sees what urgent left.
    remaining_supplier: dict[tuple[str, str], Decimal] = {}
    for supplier in store.active_suppliers():
        sid = str(supplier.get("supplier_id") or "")
        if not sid:
            continue
        for offering in supplier.get("offerings") or []:
            if not isinstance(offering, dict):
                continue
            nom = str(offering.get("nomenclature_id") or "").strip().casefold()
            if not nom:
                continue
            remaining_supplier[(sid, nom)] = (
                _dec(offering.get("available_quantity", offering.get("available_qty")))
                or _ZERO
            )

    case_wave = dict(wave_plan.get("case_wave") or {})
    wave_by_id = {
        str(w.get("wave_id")): w
        for w in (wave_plan.get("waves") or [])
        if isinstance(w, dict) and w.get("wave_id")
    }

    # Urgent-wave primary supplier per nomenclature (for diversity vs economy).
    urgent_primary_by_nom: dict[str, str] = {}
    assignments: dict[str, list[dict[str, Any]]] = {}
    lines_out: list[dict[str, Any]] = []
    picks: list[dict[str, Any]] = []
    supplier_diversity: list[dict[str, Any]] = []
    wave_results: list[dict[str, Any]] = []

    # Process lines in wave order, then deadline within wave.
    def _line_sort(line: Mapping[str, Any]) -> tuple[Any, ...]:
        cid = str(line.get("case_id") or "")
        wid = case_wave.get(cid) or ""
        wmeta = wave_by_id.get(wid) or {}
        widx = int(wmeta.get("wave_index") or 99)
        req = _parse_date(line.get("required_date")) or date.max
        return (widx, req, cid, str(line.get("line_id") or ""))

    ordered_lines = sorted(
        [line for line in (packed.get("lines") or []) if isinstance(line, dict)],
        key=_line_sort,
    )

    for line in ordered_lines:
        cid = str(line.get("case_id") or "")
        lid = str(line.get("line_id") or "")
        nom = str(line.get("nomenclature_id") or "").strip()
        nom_key = nom.casefold()
        needed = _dec(line.get("needed_quantity")) or _ZERO
        from_wh = _dec(line.get("from_warehouse")) or _ZERO
        # Prefer optimizer remainder after warehouse; ignore allocate's supplier lock
        # for re-pick so economy can choose different suppliers on residual bank.
        remainder = needed - from_wh
        if remainder < 0:
            remainder = _ZERO
        required = line.get("required_date")
        wid = case_wave.get(cid)
        wmeta = wave_by_id.get(wid or "") or {}
        label = str(wmeta.get("label") or line.get("wave_label") or "late")
        mode: WaveMode = wave_mode or wave_mode_for_label(label)  # type: ignore[arg-type]
        if mode not in {"urgent", "economy"}:
            mode = "urgent"

        if offers_by_nom and nom:
            offer_list = list(
                offers_by_nom.get(nom) or offers_by_nom.get(nom_key) or []
            )
        else:
            offer_list = list(collect_supplier_offers(nom, bank=store)) if nom else []

        # Restore residual: add back qty that allocate reserved so we can reassign,
        # but keep warehouse lock. Rebuild offer list against remaining_supplier
        # which tracks consumption of *our* picks across waves.
        # Seed remaining from original bank minus picks so far (warehouse separate).
        offer_list = _apply_remaining_qty(offer_list, remaining_supplier, nom_key)

        top = (
            _rank_for_wave_mode(
                remainder,
                offer_list,
                required_date=required,
                today=as_of,
                wave_mode=mode,
                top_n=top_n,
            )
            if remainder > 0 and offer_list
            else []
        )
        primary = top[0] if top else None
        supplier_parts: list[dict[str, Any]] = []
        if primary and remainder > 0:
            take = min(remainder, _dec(primary.get("coverable_qty")) or remainder)
            if take > 0:
                supplier_parts.append(
                    {
                        "supplier_id": primary["supplier_id"],
                        "supplier_name": primary["supplier_name"],
                        "quantity": str(take),
                        "unit_price": str(primary.get("unit_price")),
                        "meets_deadline": primary.get("meets_deadline"),
                        "wave_mode": mode,
                        "total_cost": str(primary.get("total_cost")),
                        "overpay": str(primary.get("overpay")),
                        "lead_time_days": primary.get("lead_time_days"),
                    }
                )
                _consume_pick(
                    remaining_supplier,
                    supplier_id=str(primary["supplier_id"]),
                    nom_key=nom_key,
                    qty=take,
                )

        assign_key = f"{cid}:{lid}"
        assignments[assign_key] = supplier_parts

        if mode == "urgent" and primary and nom_key:
            urgent_primary_by_nom.setdefault(nom_key, str(primary["supplier_id"]))

        diversity_row = None
        if mode == "economy" and primary and nom_key:
            urgent_sid = urgent_primary_by_nom.get(nom_key)
            if urgent_sid and urgent_sid != primary["supplier_id"]:
                # Find urgent primary offer cost for comparison if present in top/list.
                alt_price = primary.get("unit_price")
                diversity_row = {
                    "case_id": cid,
                    "line_id": lid,
                    "nomenclature_id": nom or None,
                    "nomenclature_name": line.get("nomenclature_name"),
                    "urgent_supplier_id": urgent_sid,
                    "economy_supplier_id": primary["supplier_id"],
                    "economy_supplier_name": primary["supplier_name"],
                    "unit_price": str(alt_price) if alt_price is not None else None,
                    "meets_deadline": primary.get("meets_deadline"),
                    "reason": "более дешёвый поставщик для позднего заказа при соблюдении срока",
                }
                supplier_diversity.append(diversity_row)

        row = {
            "case_id": cid,
            "line_id": lid,
            "nomenclature_id": nom or None,
            "nomenclature_name": line.get("nomenclature_name"),
            "needed_quantity": str(needed),
            "from_warehouse": str(from_wh),
            "supplier_remainder": str(remainder),
            "required_date": required,
            "coverage_source": line.get("coverage_source"),
            "tone": line.get("tone"),
            "wave_id": wid,
            "wave_label": label,
            "wave_mode": mode,
            "top_suppliers": top[:top_n],
            "supplier_parts": supplier_parts,
            "recommended_supplier_id": primary["supplier_id"] if primary else None,
            "meets_deadline": primary["meets_deadline"] if primary else None,
            "overpay": str(primary["overpay"]) if primary else "0.00",
            "lead_time_days": primary.get("lead_time_days") if primary else None,
            "optimization_rank": primary.get("optimization_rank") if primary else None,
            "optimization_reason": primary.get("optimization_reason") if primary else None,
            "deadline_risk": bool(primary.get("deadline_risk")) if primary else False,
            "supplier_diversity": diversity_row,
        }
        lines_out.append(row)
        if primary:
            picks.append(
                {
                    "case_id": cid,
                    "line_id": lid,
                    "nomenclature_id": nom,
                    "supplier_id": primary["supplier_id"],
                    "supplier_name": primary["supplier_name"],
                    "wave_id": wid,
                    "wave_mode": mode,
                    "meets_deadline": primary["meets_deadline"],
                    "total_cost": str(primary.get("total_cost")),
                    "overpay": str(primary["overpay"]),
                    "lead_time_days": primary.get("lead_time_days"),
                    "optimization_reason": primary.get("optimization_reason"),
                }
            )

    for wave in wave_plan.get("waves") or []:
        if not isinstance(wave, dict):
            continue
        wid = str(wave.get("wave_id") or "")
        wave_lines = [line for line in lines_out if line.get("wave_id") == wid]
        wave_results.append(
            {
                **wave,
                "lines_count": len(wave_lines),
                "picks_count": sum(
                    1 for line in wave_lines if line.get("recommended_supplier_id")
                ),
                "deadline_risk_lines": sum(
                    1 for line in wave_lines if line.get("deadline_risk")
                ),
            }
        )

    return {
        "allocation": packed,
        "waves": wave_plan,
        "wave_results": wave_results,
        "lines": lines_out,
        "picks": picks,
        "assignments": assignments,
        "supplier_diversity": supplier_diversity,
        "optimization_formula": QUEUE_OPTIMIZATION_FORMULA,
        "summary": {
            "lines_count": len(lines_out),
            "picks_count": len(picks),
            "deadline_risk_lines": sum(1 for line in lines_out if line.get("deadline_risk")),
            "diversity_count": len(supplier_diversity),
            "waves_count": len(wave_results),
            **(packed.get("summary") or {}),
        },
    }


__all__ = [
    "OPTIMIZATION_FORMULA",
    "QUEUE_OPTIMIZATION_FORMULA",
    "offer_meets_deadline",
    "optimize_case_coverage",
    "optimize_queue_coverage",
    "optimize_supplier_offers",
    "rank_offers_for_need",
]
