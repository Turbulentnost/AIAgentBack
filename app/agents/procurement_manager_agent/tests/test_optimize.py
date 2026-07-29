"""Unit tests for deadline → overpay → speed procurement optimizer."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.agents.procurement_manager_agent.allocation import allocate_materials_by_deadline
from app.agents.procurement_manager_agent.material_bank import reset_material_bank_for_tests
from app.agents.procurement_manager_agent.optimize import (
    offer_meets_deadline,
    optimize_case_coverage,
    optimize_supplier_offers,
)
from app.agents.procurement_manager_agent.supplier_ranking import rank_supplier_offers
from app.agents.procurement_manager_agent.tests.test_allocation import _case


def test_deadline_beats_cheaper_slow_supplier() -> None:
    today = date(2026, 7, 24)
    required = date(2026, 7, 28)  # 4 days left
    offers = [
        {
            "supplier_id": "cheap-slow",
            "supplier_name": "Дешёвый долгий",
            "nomenclature_id": "steel",
            "unit_price": Decimal("80"),
            "available_qty": Decimal("100"),
            "lead_time_days": 14,
        },
        {
            "supplier_id": "fast-pricier",
            "supplier_name": "Быстрый дороже",
            "nomenclature_id": "steel",
            "unit_price": Decimal("120"),
            "available_qty": Decimal("100"),
            "lead_time_days": 3,
        },
    ]
    top = optimize_supplier_offers(
        Decimal("10"),
        offers,
        required_date=required,
        today=today,
        top_n=2,
    )
    assert top[0]["supplier_id"] == "fast-pricier"
    assert top[0]["meets_deadline"] is True
    assert top[1]["supplier_id"] == "cheap-slow"
    assert top[1]["meets_deadline"] is False
    assert "срок ок" in (top[0]["optimization_reason"] or "")


def test_overpay_considered_vs_cheaper_large_lot() -> None:
    """Cheap large lot can lose when overpay makes total cost higher."""
    offers = [
        {
            "supplier_id": "lot-cheap",
            "supplier_name": "Лот дешёвый",
            "nomenclature_id": "bolt",
            "unit_price": Decimal("10"),
            "available_qty": Decimal("1000"),
            "lot_size": Decimal("100"),
            "lead_time_days": 2,
        },
        {
            "supplier_id": "exact-dearer",
            "supplier_name": "Точный дороже",
            "nomenclature_id": "bolt",
            "unit_price": Decimal("12"),
            "available_qty": Decimal("1000"),
            "lead_time_days": 2,
        },
    ]
    # Need 10: lot forces buy 100 → cost 1000, overpay 900
    # Exact: buy 10 → cost 120
    top = optimize_supplier_offers(
        Decimal("10"),
        offers,
        required_date=None,
        today=date(2026, 7, 24),
        top_n=2,
    )
    assert top[0]["supplier_id"] == "exact-dearer"
    assert top[0]["overpay"] == Decimal("0.00")
    assert top[1]["supplier_id"] == "lot-cheap"
    assert top[1]["overpay"] == Decimal("900.00")


def test_urgency_earlier_required_date_gets_bank_stock_first() -> None:
    bank = reset_material_bank_for_tests()
    early = _case(
        "early",
        required="2026-07-20T00:00:00",
        lines=[("l1", "steel", "150", "2026-07-20T00:00:00")],
    )
    late = _case(
        "late",
        required="2026-07-28T00:00:00",
        lines=[("l2", "steel", "100", "2026-07-28T00:00:00")],
    )
    result = allocate_materials_by_deadline([late, early], bank=bank)
    early_line = result["case_index"]["early"]["lines"][0]
    late_line = result["case_index"]["late"]["lines"][0]
    assert Decimal(early_line["from_warehouse"]) == Decimal("150")
    assert Decimal(late_line["from_warehouse"]) == Decimal("20")


def test_optimize_case_coverage_bank_first_then_supplier_rank() -> None:
    bank = reset_material_bank_for_tests()
    positions = [
        {
            "line_id": "l1",
            "nomenclature_id": "steel",
            "nomenclature_name": "Сталь",
            "quantity": "200",
            "unit": "кг",
            "required_date": "2026-07-30T00:00:00",
        }
    ]
    plan = optimize_case_coverage(
        positions,
        bank=bank,
        today=date(2026, 7, 24),
        top_n=3,
        case_required_date="2026-07-30T00:00:00",
    )
    assert plan["lines"]
    line = plan["lines"][0]
    # Seed warehouse steel ≈ 170; remainder goes to suppliers.
    assert Decimal(line["from_warehouse"]) > 0
    assert Decimal(line["supplier_remainder"]) > 0
    assert line["top_suppliers"]
    assert line["recommended_supplier_id"]
    assert "optimization_formula" in plan


def test_offer_meets_deadline_helpers() -> None:
    today = date(2026, 7, 24)
    assert offer_meets_deadline(date(2026, 7, 28), 3, today=today) is True
    assert offer_meets_deadline(date(2026, 7, 28), 10, today=today) is False
    assert offer_meets_deadline(date(2026, 7, 28), None, today=today) is None
    assert offer_meets_deadline(None, 99, today=today) is True


def test_rank_supplier_offers_uses_optimizer_gates() -> None:
    today = date(2026, 7, 24)
    bank = reset_material_bank_for_tests()
    # Tight deadline: only short lead_time offers should lead.
    top = rank_supplier_offers(
        "steel",
        Decimal("10"),
        bank=bank,
        top_n=3,
        required_date=date(2026, 7, 28),
        today=today,
    )
    assert top
    assert top[0]["meets_deadline"] is True
    assert top[0].get("optimization_rank") == 1
    # Deadline-feasible rows must appear before any miss.
    saw_miss = False
    for row in top:
        if not row["meets_deadline"]:
            saw_miss = True
        elif saw_miss:
            raise AssertionError("deadline-feasible offer ranked after a miss")

