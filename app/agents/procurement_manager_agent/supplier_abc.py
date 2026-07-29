"""Classic ABC classification of suppliers by 12-month purchase spend."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from app.agents.procurement_manager_agent.material_bank import (
    MaterialBankStore,
    get_material_bank,
)

ABC_FORMULA = (
    "Классический ABC по объёму закупок за 12 месяцев: "
    "A — накопительно ~80% суммы, B — следующие ~15%, C — остаток ~5%."
)

ABC_A_CUMULATIVE = Decimal("0.80")
ABC_B_CUMULATIVE = Decimal("0.95")

ABC_CLASS_RANK: dict[str | None, int] = {
    "A": 0,
    "B": 1,
    "C": 2,
    None: 3,
}

_CACHE_PATH = Path(__file__).resolve().parent / "data" / "supplier_abc_cache.json"
_LOCK = threading.RLock()
_LAST_REFRESH_AT: datetime | None = None

_ZERO = Decimal("0")
_ONE = Decimal("1")
_SHARE_Q = Decimal("0.0001")


@dataclass(frozen=True)
class AbcClassResult:
    supplier_id: str
    abc_class: str
    spend: Decimal
    abc_spend_share: Decimal
    cumulative_share: Decimal
    rank: int


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return _ZERO


def compute_abc_classes(
    spend_by_supplier: dict[str, Decimal | float | int | str],
    *,
    a_threshold: Decimal = ABC_A_CUMULATIVE,
    b_threshold: Decimal = ABC_B_CUMULATIVE,
) -> dict[str, AbcClassResult]:
    """Assign A/B/C by cumulative spend share (sorted desc).

    Classic rule: after sorting by spend desc, a supplier is A while previous
    cumulative share < 80%, B while previous < 95%, otherwise C. The supplier
    that crosses a threshold stays in the band it entered.
    """
    rows: list[tuple[str, Decimal]] = []
    for supplier_id, raw in spend_by_supplier.items():
        sid = str(supplier_id or "").strip()
        if not sid:
            continue
        spend = _dec(raw)
        if spend < 0:
            spend = _ZERO
        rows.append((sid, spend))
    if not rows:
        return {}
    rows.sort(key=lambda item: (-item[1], item[0]))
    total = sum((spend for _, spend in rows), _ZERO) or _ONE

    out: dict[str, AbcClassResult] = {}
    cumulative = _ZERO
    for index, (sid, spend) in enumerate(rows, start=1):
        prev = cumulative
        cumulative += spend / total
        share = (spend / total).quantize(_SHARE_Q, rounding=ROUND_HALF_UP)
        if prev < a_threshold:
            abc = "A"
        elif prev < b_threshold:
            abc = "B"
        else:
            abc = "C"
        out[sid] = AbcClassResult(
            supplier_id=sid,
            abc_class=abc,
            spend=spend,
            abc_spend_share=share,
            cumulative_share=cumulative.quantize(_SHARE_Q, rounding=ROUND_HALF_UP),
            rank=index,
        )
    return out


def abc_sort_key(abc_class: str | None) -> int:
    return ABC_CLASS_RANK.get(abc_class if abc_class in {"A", "B", "C"} else None, 3)


def spend_proxy_from_bank(bank: MaterialBankStore | None = None) -> dict[str, Decimal]:
    """Fallback spend proxy from active offerings when 1C history is unavailable."""
    store = bank or get_material_bank()
    spend: dict[str, Decimal] = {}
    for supplier in store.active_suppliers():
        sid = str(supplier.get("supplier_id") or "").strip()
        if not sid:
            continue
        total = _ZERO
        for offering in supplier.get("offerings") or []:
            if not isinstance(offering, dict):
                continue
            price = _dec(offering.get("unit_price"))
            qty = _dec(
                offering.get("available_quantity", offering.get("available_qty"))
            )
            if price > 0 and qty > 0:
                total += price * qty
        history = supplier.get("purchase_history") or supplier.get("spend_12m")
        if history is not None and not isinstance(history, (list, dict)):
            total = max(total, _dec(history))
        if isinstance(history, list):
            for row in history:
                if isinstance(row, dict):
                    total += _dec(row.get("amount") or row.get("sum") or row.get("spend"))
        spend[sid] = total
    return spend


def load_abc_cache(path: Path | None = None) -> dict[str, Any]:
    cache_path = path or _CACHE_PATH
    with _LOCK:
        if not cache_path.exists():
            return {"computed_at": None, "period_days": 365, "suppliers": {}}
        try:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"computed_at": None, "period_days": 365, "suppliers": {}}
        if not isinstance(raw, dict):
            return {"computed_at": None, "period_days": 365, "suppliers": {}}
        raw.setdefault("suppliers", {})
        return raw


def save_abc_cache(payload: dict[str, Any], path: Path | None = None) -> None:
    cache_path = path or _CACHE_PATH
    with _LOCK:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def apply_abc_to_bank(
    classes: dict[str, AbcClassResult],
    *,
    bank: MaterialBankStore | None = None,
) -> int:
    """Annotate in-memory bank suppliers with abc_class / abc_spend_share."""
    store = bank or get_material_bank()
    updated = 0
    with _LOCK:
        suppliers = store._data.get("suppliers") or []  # noqa: SLF001
        for supplier in suppliers:
            if not isinstance(supplier, dict):
                continue
            sid = str(supplier.get("supplier_id") or "").strip()
            result = classes.get(sid)
            if result is None:
                continue
            supplier["abc_class"] = result.abc_class
            supplier["abc_spend_share"] = str(result.abc_spend_share)
            supplier["abc_spend"] = str(result.spend)
            updated += 1
    return updated


def get_cached_abc_class(supplier_id: str) -> str | None:
    cache = load_abc_cache()
    row = (cache.get("suppliers") or {}).get(str(supplier_id))
    if isinstance(row, dict):
        value = row.get("abc_class")
        return str(value) if value in {"A", "B", "C"} else None
    return None


async def refresh_supplier_abc_classes(
    *,
    mcp: Any | None = None,
    force: bool = False,
    bank: MaterialBankStore | None = None,
    spend_by_supplier: dict[str, Decimal | float | int | str] | None = None,
    min_interval_seconds: int = 86400,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Recompute ABC (≤1/day auto unless force). Persist cache + annotate bank."""
    global _LAST_REFRESH_AT
    now = datetime.now(UTC)
    if not force and _LAST_REFRESH_AT is not None:
        elapsed = (now - _LAST_REFRESH_AT).total_seconds()
        if elapsed < min_interval_seconds:
            return {
                "updated": 0,
                "skipped": True,
                "reason": "throttle",
                "retry_after_seconds": int(min_interval_seconds - elapsed),
            }

    spend = dict(spend_by_supplier or {})
    if not spend and mcp is not None:
        try:
            payload = await mcp.call_capability(
                "read_procurement_get_supplier_history",
                {
                    "from": (now - timedelta(days=365)).date().isoformat(),
                    "to": now.date().isoformat(),
                },
            )
            rows = (
                payload.get("suppliers")
                or payload.get("items")
                or payload.get("history")
                or []
            )
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    sid = str(
                        row.get("supplier_id")
                        or row.get("Контрагент_Key")
                        or row.get("Ref_Key")
                        or ""
                    ).strip()
                    if not sid:
                        continue
                    amount = _dec(
                        row.get("amount")
                        or row.get("spend")
                        or row.get("Сумма")
                        or row.get("total")
                    )
                    spend[sid] = spend.get(sid, _ZERO) + amount
        except Exception:  # noqa: BLE001
            spend = {}

    if not spend:
        return await _refresh_from_proxy(
            bank=bank,
            force=force,
            cache_path=cache_path,
            now=now,
        )

    classes = compute_abc_classes(spend)
    updated = apply_abc_to_bank(classes, bank=bank)
    payload = {
        "computed_at": now.isoformat(),
        "period_days": 365,
        "formula": ABC_FORMULA,
        "suppliers": {
            sid: {
                "abc_class": item.abc_class,
                "abc_spend_share": str(item.abc_spend_share),
                "spend": str(item.spend),
                "rank": item.rank,
            }
            for sid, item in classes.items()
        },
    }
    save_abc_cache(payload, path=cache_path)
    _LAST_REFRESH_AT = now
    return {"updated": updated, "skipped": False, "classes": len(classes)}


async def _refresh_from_proxy(
    *,
    bank: MaterialBankStore | None,
    force: bool,
    cache_path: Path | None,
    now: datetime,
) -> dict[str, Any]:
    global _LAST_REFRESH_AT
    spend = spend_proxy_from_bank(bank)
    classes = compute_abc_classes(spend)
    updated = apply_abc_to_bank(classes, bank=bank)
    save_abc_cache(
        {
            "computed_at": now.isoformat(),
            "period_days": 365,
            "formula": ABC_FORMULA,
            "source": "bank_proxy",
            "suppliers": {
                sid: {
                    "abc_class": item.abc_class,
                    "abc_spend_share": str(item.abc_spend_share),
                    "spend": str(item.spend),
                    "rank": item.rank,
                }
                for sid, item in classes.items()
            },
        },
        path=cache_path,
    )
    _LAST_REFRESH_AT = now
    return {
        "updated": updated,
        "skipped": False,
        "classes": len(classes),
        "source": "bank_proxy",
        "force": force,
    }


def reset_abc_refresh_state_for_tests() -> None:
    global _LAST_REFRESH_AT
    _LAST_REFRESH_AT = None


__all__ = [
    "ABC_A_CUMULATIVE",
    "ABC_B_CUMULATIVE",
    "ABC_CLASS_RANK",
    "ABC_FORMULA",
    "AbcClassResult",
    "abc_sort_key",
    "apply_abc_to_bank",
    "compute_abc_classes",
    "get_cached_abc_class",
    "load_abc_cache",
    "refresh_supplier_abc_classes",
    "reset_abc_refresh_state_for_tests",
    "save_abc_cache",
    "spend_proxy_from_bank",
]
