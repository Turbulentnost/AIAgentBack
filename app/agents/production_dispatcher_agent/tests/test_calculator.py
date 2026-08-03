from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.agents.production_dispatcher_agent.calculator import (
    calculate_dispatcher_assessment,
)
from app.agents.production_dispatcher_agent.schemas import (
    DispatcherCaseInput,
    DispatcherNeedLine,
    DispatcherOutcome,
    DispatcherSupplyItem,
    DispatcherUrgency,
)


def _case(**overrides):
    payload = {
        "case_id": "case-1",
        "case_number": "ТЗ-1",
        "source_type": "reorder_point",
        "source_1c_ref": "ref-1",
        "warehouse_1c_ref": "wh-main",
        "stock_growth_coefficient": Decimal("1.5"),
    }
    payload.update(overrides)
    return DispatcherCaseInput.model_validate(payload)


def _need(**overrides):
    payload = {
        "line_id": "1",
        "nomenclature_id": "mat-1",
        "nomenclature_name": "Материал",
        "unit": "шт",
        "quantity": Decimal("30"),
        "warehouse_id": "wh-main",
        "minimum_stock": Decimal("10"),
        "maximum_stock": Decimal("30"),
        "production_deficit": Decimal("0"),
    }
    payload.update(overrides)
    return DispatcherNeedLine.model_validate(payload)


def _supply(**overrides):
    payload = {
        "supply_id": "s1",
        "source_type": "warehouse",
        "nomenclature_id": "mat-1",
        "unit": "шт",
        "quantity": Decimal("20"),
        "warehouse_id": "wh-main",
    }
    payload.update(overrides)
    return DispatcherSupplyItem.model_validate(payload)


def test_minimum_uses_stock_growth_coefficient():
    result = calculate_dispatcher_assessment(
        case=_case(),
        needs=[_need()],
        supplies=[_supply(quantity=Decimal("12"))],
        calculated_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    line = result.positions[0]
    assert line.minimum_stock == Decimal("15.000")
    assert line.maximum_stock == Decimal("45.000")
    assert line.below_minimum is True
    assert line.urgency is DispatcherUrgency.CRITICAL


def test_expected_stock_is_not_free_stock():
    result = calculate_dispatcher_assessment(
        case=_case(stock_growth_coefficient=Decimal("1")),
        needs=[_need(minimum_stock=Decimal("10"), maximum_stock=Decimal("30"))],
        supplies=[
            _supply(supply_id="free", quantity=Decimal("8")),
            _supply(
                supply_id="transit",
                source_type="in_transit",
                quantity=Decimal("20"),
            ),
            _supply(
                supply_id="wip",
                source_type="in_progress",
                quantity=Decimal("5"),
            ),
        ],
        calculated_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    line = result.positions[0]
    assert line.free_stock == Decimal("8")
    assert line.expected_total == Decimal("25")
    assert line.below_minimum is True
    assert line.forecast_stock == Decimal("33")


def test_transfer_recommended_from_other_warehouse():
    result = calculate_dispatcher_assessment(
        case=_case(stock_growth_coefficient=Decimal("1")),
        needs=[
            _need(
                minimum_stock=Decimal("0"),
                maximum_stock=Decimal("10"),
                production_deficit=Decimal("10"),
                quantity=Decimal("10"),
            )
        ],
        supplies=[
            _supply(supply_id="local", quantity=Decimal("2")),
            _supply(
                supply_id="other",
                quantity=Decimal("12"),
                warehouse_id="wh-other",
            ),
        ],
        calculated_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    line = result.positions[0]
    assert line.outcome is DispatcherOutcome.TRANSFER_PROPOSED
    assert result.decision_kind == "supply_confirmation"
