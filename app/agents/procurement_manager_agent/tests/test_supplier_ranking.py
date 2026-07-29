from __future__ import annotations

from decimal import Decimal

from app.agents.procurement_manager_agent.material_bank import reset_material_bank_for_tests
from app.agents.procurement_manager_agent.pricing import supplier_price_bounds
from app.agents.procurement_manager_agent.supplier_ranking import (
    SCORE_FORMULA,
    collect_supplier_offers,
    rank_supplier_offers,
)


def test_seed_offerings_have_price_and_available_qty() -> None:
    bank = reset_material_bank_for_tests()
    offers = collect_supplier_offers("steel", bank=bank)
    assert len(offers) >= 3
    for offer in offers:
        assert offer["unit_price"] > 0
        assert offer["available_qty"] > 0


def test_rank_prefers_cheaper_full_coverage() -> None:
    bank = reset_material_bank_for_tests()
    need = Decimal("10")
    top = rank_supplier_offers("steel", need, bank=bank, top_n=3)
    assert len(top) == 3
    assert [row["rank"] for row in top] == [1, 2, 3]
    # Without deadline, minimize total_cost; ranks are 1..n.
    assert top[0]["optimization_rank"] == 1
    full = [row for row in top if row["coverable_qty"] >= need]
    assert full, "expected at least one full-coverage candidate in top-3"
    assert top[0]["coverable_qty"] == min(need, top[0]["available_qty"])
    costs = [row["total_cost"] for row in top]
    assert costs == sorted(costs)


def test_score_formula_describes_priority_gates() -> None:
    assert "meets_deadline" in SCORE_FORMULA or "срок" in SCORE_FORMULA
    assert "overpay" in SCORE_FORMULA or "переплат" in SCORE_FORMULA
    assert "lead_time" in SCORE_FORMULA


def test_partial_coverage_reason() -> None:
    bank = reset_material_bank_for_tests()
    # Huge need forces partial coverage for typical seed capacities (20..99).
    top = rank_supplier_offers("steel", Decimal("1000"), bank=bank, top_n=3)
    assert top
    assert top[0]["coverage_ratio"] < 1
    assert "частично" in top[0]["reason"]


def test_top_prices_align_with_global_min_max() -> None:
    bank = reset_material_bank_for_tests()
    bounds = supplier_price_bounds(bank)["steel"]
    offers = collect_supplier_offers("steel", bank=bank)
    offer_prices = [o["unit_price"] for o in offers]
    assert min(offer_prices) == bounds["price_min"]
    assert max(offer_prices) == bounds["price_max"]
