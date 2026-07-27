"""Meter-based positions split into separate length batches."""

from decimal import Decimal
from types import SimpleNamespace

from app.agents.procurement_manager_agent.batches import (
    build_batches_from_coverage,
    split_meter_pieces,
)


def test_split_meter_pieces_two_cuts() -> None:
    pieces = split_meter_pieces(Decimal("11.4"), seed=1)
    assert len(pieces) >= 2
    assert sum(pieces) == Decimal("11.4")
    assert all(p > 0 for p in pieces)


def test_split_meter_pieces_explicit() -> None:
    pieces = split_meter_pieces(
        Decimal("11.4"),
        explicit=[Decimal("5.1"), Decimal("6.3")],
    )
    assert pieces == [Decimal("5.1"), Decimal("6.3")]


def test_build_batches_splits_meters_same_day() -> None:
    position = SimpleNamespace(
        line_id="L1",
        id="L1",
        quantity=Decimal("11.4"),
        unit="м",
        required_date=None,
        cancelled=False,
        raw_payload={"meter_pieces": ["5.1", "6.3"]},
    )
    batches = build_batches_from_coverage(
        positions=[position],
        coverage_lines=[
            {
                "line_id": "L1",
                "from_warehouse": 0,
                "from_supplier": 0,
                "coverage_source": "none",
            }
        ],
        workspace={},
    )
    assert len(batches) == 2
    assert batches[0]["quantity"] == 5.1
    assert batches[1]["quantity"] == 6.3
    assert batches[0]["batch_no"] != batches[1]["batch_no"]
    assert batches[0].get("is_meter_piece") is True


def test_non_meter_stays_single_batch() -> None:
    position = SimpleNamespace(
        line_id="L2",
        id="L2",
        quantity=Decimal("22"),
        unit="шт",
        required_date=None,
        cancelled=False,
        raw_payload={},
    )
    batches = build_batches_from_coverage(
        positions=[position],
        coverage_lines=[{"line_id": "L2", "coverage_source": "none"}],
        workspace={},
    )
    assert len(batches) == 1
    assert batches[0]["quantity"] == 22.0


def test_mixed_coverage_splits_warehouse_and_supplier_batches() -> None:
    """Mixed line → warehouse batch + purchase batch (not mixed label on both)."""
    position = SimpleNamespace(
        line_id="L-filter",
        id="L-filter",
        quantity=Decimal("72"),
        unit="шт",
        required_date=None,
        cancelled=False,
        raw_payload={},
    )
    batches = build_batches_from_coverage(
        positions=[position],
        coverage_lines=[
            {
                "line_id": "L-filter",
                "from_warehouse": 55,
                "from_supplier": 17,
                "coverage_source": "mixed",
                "used_suppliers": [
                    {
                        "supplier_id": "s5",
                        "supplier_name": "ООО «Индустриум-005»",
                        "quantity": 17,
                    }
                ],
            }
        ],
        workspace={},
    )
    assert len(batches) == 2
    wh, buy = batches
    assert wh["coverage_source"] == "warehouse"
    assert wh["quantity"] == 55.0
    assert wh["supplier_name"] == "Склад"
    assert buy["coverage_source"] == "supplier"
    assert buy["quantity"] == 17.0
    assert buy["supplier_name"] == "ООО «Индустриум-005»"
