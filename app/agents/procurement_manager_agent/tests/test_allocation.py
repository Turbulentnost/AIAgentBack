from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.agents.procurement_manager_agent.allocation import allocate_materials_by_deadline
from app.agents.procurement_manager_agent.material_bank import (
    get_material_bank,
    reset_material_bank_for_tests,
)


def _case(
    case_id: str,
    *,
    required: str,
    lines: list[tuple[str, str, str, str]],
) -> SimpleNamespace:
    """lines: (line_id, nomenclature_id, quantity, required_date|None)."""
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


def test_seed_bank_has_100_suppliers_and_3_warehouses() -> None:
    bank = reset_material_bank_for_tests()
    assert len(bank.warehouses()) == 3
    assert len(bank.active_suppliers()) == 100
    assert len(bank.stock_lines()) >= 3


def test_earlier_order_reserves_warehouse_stock() -> None:
    bank = reset_material_bank_for_tests()
    # steel warehouse stock in seed: 120 + 50 = 170
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
    index = result["case_index"]
    early_line = index["early"]["lines"][0]
    late_line = index["late"]["lines"][0]

    assert Decimal(early_line["from_warehouse"]) == Decimal("150")
    assert early_line["tone"] == "ready"
    # Remaining warehouse for steel = 20; late takes it, rest from suppliers or deficit
    assert Decimal(late_line["from_warehouse"]) == Decimal("20")
    assert Decimal(late_line["needed_quantity"]) == Decimal("100")
    assert Decimal(late_line["from_warehouse"]) < Decimal(late_line["needed_quantity"])


def test_fully_uncovered_order_counts_for_kpi() -> None:
    bank = reset_material_bank_for_tests()
    unknown = _case(
        "unknown",
        required="2026-07-21T00:00:00",
        lines=[("l1", "totally-unknown-nom-xyz", "10", "2026-07-21T00:00:00")],
    )
    result = allocate_materials_by_deadline([unknown], bank=bank)
    summary = result["summary"]
    assert summary["uncovered_orders_count"] == 1
    assert summary["uncovered_positions_count"] == 1
    assert summary["active_suppliers_count"] == 100
    assert summary["warehouses_count"] == 3
    assert result["case_index"]["unknown"]["tone"] == "uncovered"


def test_uncovered_positions_are_line_counts_not_qty_sum() -> None:
    bank = reset_material_bank_for_tests()
    case = _case(
        "pipes",
        required="2026-07-22T00:00:00",
        lines=[
            ("a", "missing-a", "500", "2026-07-22T00:00:00"),
            ("b", "missing-b", "700", "2026-07-22T00:00:00"),
        ],
    )
    result = allocate_materials_by_deadline([case], bank=bank)
    # Two uncovered lines — not 1200 meters/units.
    assert result["summary"]["uncovered_positions_count"] == 2


def test_kpi_uncovered_bounded_by_queue_size() -> None:
    """With N cases in the manager queue, uncovered KPIs cannot exceed N / total lines."""
    bank = reset_material_bank_for_tests()
    cases = [
        _case(
            f"order-{index}",
            required=f"2026-07-{(20 + index % 9):02d}T00:00:00",
            lines=[
                (
                    f"l{index}a",
                    f"missing-nom-{index}-a",
                    "25",
                    f"2026-07-{(20 + index % 9):02d}T00:00:00",
                ),
                (
                    f"l{index}b",
                    f"missing-nom-{index}-b",
                    "40",
                    f"2026-07-{(20 + index % 9):02d}T00:00:00",
                ),
            ],
        )
        for index in range(12)
    ]
    result = allocate_materials_by_deadline(cases, bank=bank)
    summary = result["summary"]
    queue_n = summary["total_orders_count"]
    assert queue_n == 12
    assert summary["uncovered_orders_count"] <= queue_n
    assert summary["uncovered_positions_count"] <= summary["positions_count"]
    assert summary["positions_count"] == 24
    assert summary["active_suppliers_count"] == 100


def test_manager_queue_statuses_exclude_engineer_stages() -> None:
    from app.agents.procurement_manager_agent.service import (
        MANAGER_QUEUE_STATUSES,
        case_in_manager_queue,
    )

    assert "human_required" not in MANAGER_QUEUE_STATUSES
    assert "agent_waiting" not in MANAGER_QUEUE_STATUSES
    assert "data_check" not in MANAGER_QUEUE_STATUSES
    assert case_in_manager_queue(
        current_agent_id="purchase_manager_agent",
        status="human_required",
    )
    assert not case_in_manager_queue(
        current_agent_id="production_preparation_engineer_agent",
        status="human_required",
    )
    assert case_in_manager_queue(
        current_agent_id=None,
        status="purchase_draft",
    )


def test_mixed_source_when_warehouse_and_supplier_used() -> None:
    bank = get_material_bank()
    # Demand above warehouse residual for bolts but suppliers cover rest.
    case = _case(
        "bolts",
        required="2026-07-19T00:00:00",
        lines=[("b1", "30.02.00015", "2200", "2026-07-19T00:00:00")],
    )
    result = allocate_materials_by_deadline([case], bank=bank)
    line = result["case_index"]["bolts"]["lines"][0]
    assert Decimal(line["from_warehouse"]) > 0
    assert Decimal(line["from_supplier"]) > 0
    assert line["coverage_source"] == "mixed"
    assert line["supplier_parts"]
    assert all(Decimal(str(part["quantity"])) > 0 for part in line["supplier_parts"])
    by_nom = next(
        row
        for row in result["by_nomenclature"]
        if str(row.get("nomenclature_id") or "") == "30.02.00015"
    )
    assert by_nom["used_suppliers"]
    assert by_nom["coverage_source"] == "mixed"
    assert Decimal(by_nom["from_warehouse"]) > 0
    assert Decimal(by_nom["from_supplier"]) > 0
    # «Все позиции» reads these fields from by_nomenclature via AllPositionsRow.
    from app.agents.procurement_manager_agent.schemas import AllPositionsRow

    payload = AllPositionsRow(
        nomenclature_id=by_nom.get("nomenclature_id"),
        nomenclature_name=by_nom.get("nomenclature_name"),
        quantity=Decimal(str(by_nom["needed_quantity"])),
        amount_formula="test",
        coverage_source=by_nom["coverage_source"],
        coverage_source_label=by_nom.get("coverage_source_label"),
        from_warehouse=Decimal(str(by_nom["from_warehouse"])),
        from_supplier=Decimal(str(by_nom["from_supplier"])),
    ).model_dump(mode="json")
    assert "from_warehouse" in payload
    assert "from_supplier" in payload
    assert Decimal(str(payload["from_warehouse"])) == Decimal(by_nom["from_warehouse"])
    assert Decimal(str(payload["from_supplier"])) == Decimal(by_nom["from_supplier"])


def test_partial_coverage_case_tone_is_attention() -> None:
    """Partial cover must be «Требуют внимания», not «Полностью необеспечен»."""
    bank = reset_material_bank_for_tests()
    case = _case(
        "partial",
        required="2026-07-21T00:00:00",
        lines=[
            ("ok", "steel", "10", "2026-07-21T00:00:00"),
            ("miss", "missing-seal-kit", "5", "2026-07-21T00:00:00"),
        ],
    )
    result = allocate_materials_by_deadline([case], bank=bank)
    coverage = result["case_index"]["partial"]
    assert coverage["tone"] == "attention"
    assert coverage["label"] == "Требуют внимания"
    tones = {line["tone"] for line in coverage["lines"]}
    assert "ready" in tones
    assert "uncovered" in tones
    assert Decimal(coverage["covered_quantity"]) > 0
    assert Decimal(coverage["deficit_quantity"]) > 0
    # covered_count includes partially/fully covered lines (not only ready-only).
    assert coverage["covered_count"] == 1
    assert coverage["uncovered_positions_count"] == 1


def test_name_fallback_matches_bank_when_id_is_guid() -> None:
    """1C-style GUID nomenclature_id still matches bank stock by name."""
    bank = reset_material_bank_for_tests()
    case = _case(
        "guid-nom",
        required="2026-07-20T00:00:00",
        lines=[
            (
                "l1",
                "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "10",
                "2026-07-20T00:00:00",
            )
        ],
    )
    # Override name to steel's warehouse title after _case built with id as name.
    case.positions[0].nomenclature_name = "Сталь 20 лист 5 мм"
    result = allocate_materials_by_deadline([case], bank=bank)
    line = result["case_index"]["guid-nom"]["lines"][0]
    assert Decimal(line["covered_quantity"]) == Decimal("10")
    assert line["tone"] == "ready"
    assert line["coverage_source"] == "warehouse"
    assert line["supplier_parts"] == []
    assert result["case_index"]["guid-nom"]["tone"] == "ready"
    by_nom = next(
        row
        for row in result["by_nomenclature"]
        if row.get("nomenclature_name") == "Сталь 20 лист 5 мм"
        or "a1b2c3d4" in str(row.get("nomenclature_id") or "")
    )
    assert by_nom["used_suppliers"] == []
    assert by_nom["coverage_source"] == "warehouse"
