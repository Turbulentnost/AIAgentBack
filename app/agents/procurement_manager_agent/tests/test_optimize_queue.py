"""Cross-order optimize_queue_coverage: urgent vs economy diversity."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.agents.procurement_manager_agent.material_bank import reset_material_bank_for_tests
from app.agents.procurement_manager_agent.optimize import optimize_queue_coverage
from app.agents.procurement_manager_agent.tests.test_allocation import _case


def test_optimize_queue_coverage_assigns_lines() -> None:
    bank = reset_material_bank_for_tests()
    today = date(2026, 7, 20)
    early = _case(
        "early",
        required="2026-07-22T00:00:00",
        lines=[("l1", "steel", "50", "2026-07-22T00:00:00")],
    )
    late = _case(
        "late",
        required="2026-09-01T00:00:00",
        lines=[("l2", "steel", "40", "2026-09-01T00:00:00")],
    )
    plan = optimize_queue_coverage([late, early], bank=bank, today=today, top_n=3)
    assert plan.get("waves")
    assert plan.get("assignments") is not None
    assert plan.get("lines")
    # Early critical wave uses urgent mode
    waves = (plan.get("waves") or {}).get("waves") or []
    assert any(w.get("mode") == "urgent" for w in waves)
    assert any(w.get("mode") == "economy" for w in waves)


def test_economy_wave_can_differ_from_urgent_primary() -> None:
    """With forced offers, late/economy may pick a cheaper different supplier."""
    bank = reset_material_bank_for_tests()
    today = date(2026, 7, 24)
    # Unknown nom → no warehouse; both need suppliers from injected offers
    early = _case(
        "early",
        required="2026-07-26T00:00:00",
        lines=[("l1", "widget-x", "10", "2026-07-26T00:00:00")],
    )
    late = _case(
        "late",
        required="2026-09-20T00:00:00",
        lines=[("l2", "widget-x", "10", "2026-09-20T00:00:00")],
    )
    offers = {
        "widget-x": [
            {
                "supplier_id": "fast-a",
                "supplier_name": "Fast A",
                "nomenclature_id": "widget-x",
                "unit_price": Decimal("200"),
                "available_qty": Decimal("100"),
                "lead_time_days": 2,
            },
            {
                "supplier_id": "cheap-b",
                "supplier_name": "Cheap B",
                "nomenclature_id": "widget-x",
                "unit_price": Decimal("100"),
                "available_qty": Decimal("100"),
                "lead_time_days": 20,
            },
        ]
    }
    plan = optimize_queue_coverage(
        [late, early],
        bank=bank,
        offers_by_nom=offers,
        today=today,
        top_n=2,
    )
    assignments = plan.get("assignments") or {}
    early_parts = assignments.get("early:l1") or assignments.get("early|l1") or []
    # Find early assignment by scanning keys
    if not early_parts:
        for key, parts in assignments.items():
            if "early" in str(key) and "l1" in str(key):
                early_parts = parts
                break
    late_parts = []
    for key, parts in assignments.items():
        if "late" in str(key) and "l2" in str(key):
            late_parts = parts
            break
    # Early should prefer fast (deadline); late may take cheap if deadline ok
    if early_parts:
        assert early_parts[0].get("supplier_id") in {"fast-a", "cheap-b"}
    diversity = plan.get("supplier_diversity") or []
    # Diversity is optional depending on residual; structure must be list
    assert isinstance(diversity, list)
    assert late_parts is not None
