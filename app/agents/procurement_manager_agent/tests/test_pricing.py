from __future__ import annotations

from decimal import Decimal

from app.agents.procurement_manager_agent.allocation import allocate_materials_by_deadline
from app.agents.procurement_manager_agent.material_bank import reset_material_bank_for_tests
from app.agents.procurement_manager_agent.pricing import (
    estimate_nomenclature_amount,
    greedy_cover_cost,
    supplier_price_bounds,
)
from app.agents.procurement_manager_agent.supplier_ranking import collect_supplier_offers
from app.agents.procurement_manager_agent.tests.test_allocation import _case


def test_seed_has_overlapping_supplier_prices() -> None:
    bank = reset_material_bank_for_tests()
    bounds = supplier_price_bounds(bank)
    assert "steel" in bounds
    assert bounds["steel"]["price_min"] < bounds["steel"]["price_max"]
    assert bounds["steel"]["suppliers_count"] >= 3
    # Typical order nomenclature from seed materials.
    assert "10.01.00125" in bounds
    assert "30.02.00015" in bounds


def test_estimate_falls_back_to_min_price_without_offers() -> None:
    result = estimate_nomenclature_amount(
        Decimal("10"),
        price_min=Decimal("120"),
        offers=[],
    )
    assert result.amount == Decimal("1200.00")
    assert result.source == "price_min"


def test_estimate_respects_manager_override() -> None:
    result = estimate_nomenclature_amount(
        Decimal("10"),
        price_min=Decimal("120"),
        line_overrides=[
            (Decimal("4"), Decimal("200")),  # override
            (Decimal("6"), None),  # falls back to min without offers
        ],
        offers=[],
    )
    # 4*200 + 6*120 = 800 + 720 = 1520
    assert result.amount == Decimal("1520.00")
    assert result.source == "смешанный"


def test_warehouse_coverage_amount_is_zero() -> None:
    result = estimate_nomenclature_amount(
        Decimal("10"),
        price_min=Decimal("120"),
        coverage_source="warehouse",
        from_supplier=Decimal("0"),
        offers=[{"unit_price": Decimal("120"), "available_qty": Decimal("100")}],
    )
    assert result.amount == Decimal("0.00")
    assert result.source == "склад"


def test_greedy_cover_uses_cheapest_offers_first() -> None:
    offers = [
        {"supplier_id": "a", "unit_price": Decimal("10"), "available_qty": Decimal("5")},
        {"supplier_id": "b", "unit_price": Decimal("20"), "available_qty": Decimal("100")},
        {"supplier_id": "c", "unit_price": Decimal("8"), "available_qty": Decimal("3")},
    ]
    cover = greedy_cover_cost(Decimal("10"), offers)
    # 3*8 + 5*10 + 2*20 = 24 + 50 + 40 = 114
    assert cover.covered_qty == Decimal("10")
    assert cover.amount == Decimal("114.00")
    assert cover.source == "покрывающие офферы"


def test_greedy_cover_costs_only_coverable_qty() -> None:
    offers = [
        {"supplier_id": "a", "unit_price": Decimal("10"), "available_qty": Decimal("4")},
    ]
    cover = greedy_cover_cost(Decimal("20"), offers)
    assert cover.covered_qty == Decimal("4")
    assert cover.amount == Decimal("40.00")


def test_mixed_coverage_bills_supplier_qty_via_offers() -> None:
    offers = [
        {"supplier_id": "a", "unit_price": Decimal("12"), "available_qty": Decimal("10")},
        {"supplier_id": "b", "unit_price": Decimal("15"), "available_qty": Decimal("50")},
    ]
    result = estimate_nomenclature_amount(
        Decimal("100"),
        price_min=Decimal("12"),
        coverage_source="mixed",
        from_supplier=Decimal("30"),
        offers=offers,
    )
    # 10*12 + 20*15 = 120 + 300 = 420 — not 30*12
    assert result.amount == Decimal("420.00")
    assert result.source == "покрывающие офферы"


def test_by_nomenclature_warehouse_amount_is_zero() -> None:
    bank = reset_material_bank_for_tests()
    case = _case(
        "priced",
        required="2026-07-20T00:00:00",
        lines=[("l1", "steel", "10", "2026-07-20T00:00:00")],
    )
    result = allocate_materials_by_deadline([case], bank=bank)
    row = next(
        item
        for item in result["by_nomenclature"]
        if item.get("nomenclature_id") == "steel"
    )
    assert row["coverage_source"] == "warehouse"
    assert row["price_min"] is not None
    assert row["price_max"] is not None
    assert Decimal(row["price_min"]) <= Decimal(row["price_max"])
    assert Decimal(row["estimated_amount"]) == Decimal("0.00")
    assert row["amount_source"] == "склад"


def test_by_nomenclature_supplier_amount_uses_covering_offers() -> None:
    bank = reset_material_bank_for_tests()
    # Demand above seed warehouse residual so allocation uses suppliers.
    case = _case(
        "buy",
        required="2026-07-20T00:00:00",
        lines=[("l1", "steel", "500", "2026-07-20T00:00:00")],
    )
    result = allocate_materials_by_deadline([case], bank=bank)
    row = next(
        item
        for item in result["by_nomenclature"]
        if item.get("nomenclature_id") == "steel"
    )
    assert row["coverage_source"] in {"supplier", "mixed"}
    from_supplier = Decimal(row["from_supplier"])
    price_min = Decimal(row["price_min"])
    expected = greedy_cover_cost(
        from_supplier,
        collect_supplier_offers("steel", bank=bank),
    )
    assert expected.amount is not None
    assert Decimal(row["estimated_amount"]) == expected.amount
    assert row["amount_source"] == "покрывающие офферы"
    # Must not naively use full supplier qty × global price_min when cheapest can't cover.
    naive = (from_supplier * price_min).quantize(Decimal("0.01"))
    if expected.amount != naive:
        assert Decimal(row["estimated_amount"]) != naive
