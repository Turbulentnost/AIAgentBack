"""Unit tests for classic ABC supplier classification and ranking tie-break."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.agents.procurement_manager_agent.optimize import optimize_supplier_offers
from app.agents.procurement_manager_agent.supplier_abc import (
    compute_abc_classes,
    refresh_supplier_abc_classes,
    reset_abc_refresh_state_for_tests,
)


def test_abc_boundaries_80_15_5() -> None:
    spend = {
        "a1": Decimal("70"),
        "a2": Decimal("10"),  # cumulative 80% → still A (crosses at a2)
        "b1": Decimal("10"),  # prev 80% < 95% → B
        "b2": Decimal("5"),  # prev 90% → B, cum 95%
        "c1": Decimal("5"),  # prev 95% → C
    }
    classes = compute_abc_classes(spend)
    assert classes["a1"].abc_class == "A"
    assert classes["a2"].abc_class == "A"
    assert classes["b1"].abc_class == "B"
    assert classes["b2"].abc_class == "B"
    assert classes["c1"].abc_class == "C"
    assert sum(item.abc_spend_share for item in classes.values()) == Decimal("1.0000")


def test_abc_single_supplier_is_a() -> None:
    classes = compute_abc_classes({"only": 1000})
    assert classes["only"].abc_class == "A"


def test_abc_prefers_class_a_among_deadline_ok() -> None:
    today = date(2026, 7, 24)
    required = date(2026, 8, 10)
    offers = [
        {
            "supplier_id": "class-c",
            "supplier_name": "C дешевле",
            "nomenclature_id": "bolt",
            "unit_price": Decimal("50"),
            "available_qty": Decimal("100"),
            "lead_time_days": 3,
            "abc_class": "C",
        },
        {
            "supplier_id": "class-a",
            "supplier_name": "A дороже",
            "nomenclature_id": "bolt",
            "unit_price": Decimal("80"),
            "available_qty": Decimal("100"),
            "lead_time_days": 3,
            "abc_class": "A",
        },
    ]
    top = optimize_supplier_offers(
        Decimal("10"),
        offers,
        required_date=required,
        today=today,
        top_n=2,
    )
    assert top[0]["supplier_id"] == "class-a"
    assert top[0]["abc_class"] == "A"
    assert "ABC A" in (top[0]["optimization_reason"] or "")


async def test_abc_refresh_throttles_without_force(tmp_path) -> None:
    reset_abc_refresh_state_for_tests()
    cache = tmp_path / "abc.json"
    first = await refresh_supplier_abc_classes(
        spend_by_supplier={"s1": 100, "s2": 10},
        force=True,
        cache_path=cache,
        min_interval_seconds=86400,
    )
    assert first["skipped"] is False
    second = await refresh_supplier_abc_classes(
        spend_by_supplier={"s1": 100, "s2": 10},
        force=False,
        cache_path=cache,
        min_interval_seconds=86400,
    )
    assert second["skipped"] is True
    assert second.get("reason") == "throttle"
    forced = await refresh_supplier_abc_classes(
        spend_by_supplier={"s1": 100, "s2": 10},
        force=True,
        cache_path=cache,
        min_interval_seconds=86400,
    )
    assert forced["skipped"] is False
