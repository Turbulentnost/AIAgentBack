from __future__ import annotations

from decimal import Decimal

from app.agents.procurement_manager_agent.material_bank import reset_material_bank_for_tests
from app.agents.procurement_manager_agent.pricing import supplier_price_bounds
from app.agents.procurement_manager_agent.supplier_ranking import (
    COVERAGE_WEIGHT,
    PRICE_WEIGHT,
    SCORE_FORMULA,
    SHORTFALL_PENALTY_WEIGHT,
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
    assert top[0]["score"] >= top[1]["score"] >= top[2]["score"]
    # Winner should be among cheapest full-cover options when capacity allows.
    full = [row for row in top if row["coverable_qty"] >= need]
    assert full, "expected at least one full-coverage candidate in top-3"
    assert top[0]["coverable_qty"] == min(need, top[0]["available_qty"])
    assert top[0]["coverage_cost"] == (
        top[0]["coverable_qty"] * top[0]["unit_price"]
    ).quantize(Decimal("0.01"))


def test_score_formula_matches_weights() -> None:
    bank = reset_material_bank_for_tests()
    offers = collect_supplier_offers("30.02.00015", bank=bank)
    assert offers
    need = Decimal("100")
    prices = [o["unit_price"] for o in offers]
    min_p, max_p = min(prices), max(prices)
    span = max_p - min_p

    ranked = rank_supplier_offers("30.02.00015", need, bank=bank, top_n=5)
    assert ranked
    sample = ranked[0]
    raw = next(o for o in offers if o["supplier_id"] == sample["supplier_id"])
    coverable = min(need, raw["available_qty"])
    coverage_ratio = coverable / need
    if span == 0:
        price_score = Decimal("1")
    else:
        price_score = (max_p - raw["unit_price"]) / span
    expected = (
        PRICE_WEIGHT * price_score
        + COVERAGE_WEIGHT * coverage_ratio
        - SHORTFALL_PENALTY_WEIGHT * (Decimal("1") - coverage_ratio)
    ).quantize(Decimal("0.0001"))
    assert sample["score"] == expected
    assert "0.55" in SCORE_FORMULA
    assert "0.45" in SCORE_FORMULA


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
