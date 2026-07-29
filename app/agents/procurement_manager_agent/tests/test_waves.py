"""Tests for urgency wave bucketing and queue allocation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.agents.procurement_manager_agent.material_bank import reset_material_bank_for_tests
from app.agents.procurement_manager_agent.waves import (
    allocate_queue_with_waves,
    bucket_urgency_waves,
    classify_urgency,
    wave_mode_for_label,
)


def _case(
    case_id: str,
    *,
    required: str,
    lines: list[tuple[str, str, str, str]],
) -> SimpleNamespace:
    positions = []
    for line_id, nom_id, qty, line_required in lines:
        positions.append(
            SimpleNamespace(
                line_id=line_id,
                id=line_id,
                nomenclature_id=nom_id,
                nomenclature_name=nom_id,
                quantity=Decimal(qty),
                unit="шт",
                required_date=(
                    datetime.fromisoformat(line_required).replace(tzinfo=UTC)
                    if line_required
                    else None
                ),
                cancelled=False,
            )
        )
    return SimpleNamespace(
        id=case_id,
        required_date=datetime.fromisoformat(required).replace(tzinfo=UTC),
        positions=positions,
    )


def test_classify_urgency_buckets() -> None:
    today = date(2026, 7, 24)
    assert classify_urgency(today + timedelta(days=3), today=today) == "critical"
    assert classify_urgency(today + timedelta(days=14), today=today) == "medium"
    assert classify_urgency(today + timedelta(days=40), today=today) == "late"
    assert classify_urgency(None, today=today) == "late"


def test_wave_mode_urgent_vs_economy() -> None:
    assert wave_mode_for_label("critical") == "urgent"
    assert wave_mode_for_label("medium") == "urgent"
    assert wave_mode_for_label("late") == "economy"


def test_bucket_urgency_waves_orders_critical_first() -> None:
    today = date(2026, 7, 24)
    early = _case(
        "c-early",
        required="2026-07-26T00:00:00",
        lines=[("l1", "steel", "10", "2026-07-26T00:00:00")],
    )
    late = _case(
        "c-late",
        required="2026-09-10T00:00:00",
        lines=[("l2", "steel", "10", "2026-09-10T00:00:00")],
    )
    mid = _case(
        "c-mid",
        required="2026-08-05T00:00:00",
        lines=[("l3", "steel", "10", "2026-08-05T00:00:00")],
    )
    plan = bucket_urgency_waves([late, early, mid], today=today)
    waves = plan["waves"]
    assert waves[0]["label"] == "critical"
    assert "c-early" in waves[0]["case_ids"]
    assert plan["case_wave"]["c-early"] == waves[0]["wave_id"]
    late_wave = next(w for w in waves if w["label"] == "late")
    assert "c-late" in late_wave["case_ids"]
    assert late_wave["mode"] == "economy"


def test_allocate_queue_with_waves_locks_bank_for_urgent() -> None:
    bank = reset_material_bank_for_tests()
    today = date(2026, 7, 20)
    early = _case(
        "early",
        required="2026-07-22T00:00:00",
        lines=[("l1", "steel", "150", "2026-07-22T00:00:00")],
    )
    late = _case(
        "late",
        required="2026-09-01T00:00:00",
        lines=[("l2", "steel", "100", "2026-09-01T00:00:00")],
    )
    packed = allocate_queue_with_waves([late, early], bank=bank, today=today)
    index = packed["case_index"]
    early_line = index["early"]["lines"][0]
    late_line = index["late"]["lines"][0]
    assert Decimal(early_line["from_warehouse"]) == Decimal("150")
    assert Decimal(late_line["from_warehouse"]) == Decimal("20")
    assert packed["waves"]["waves"]
