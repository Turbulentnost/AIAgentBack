from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.agents.warehouse_picker_agent.calculator import calculate_picker_assessment
from app.agents.warehouse_picker_agent.department import is_montage_section_2_department
from app.agents.warehouse_picker_agent.schemas import (
    PickerCaseInput,
    PickerNeedLine,
    PickerOutcome,
    PickerSupplyItem,
)


def test_department_filter_matches_montage_section_2():
    assert is_montage_section_2_department("Монтажный участок №2")
    assert is_montage_section_2_department("Механический участок 2")
    assert not is_montage_section_2_department("Монтажный участок №1")
    assert not is_montage_section_2_department("Цех №2")


def test_full_issue_from_store_room():
    result = calculate_picker_assessment(
        case=PickerCaseInput(
            case_id="c1",
            case_number="НП-1",
            source_1c_ref="ref",
            department_name="Монтажный участок №2",
            warehouse_1c_ref="wh-main",
            warehouse_name="Склад комплектующих",
        ),
        needs=[
            PickerNeedLine(
                line_id="1",
                nomenclature_id="mat",
                nomenclature_name="Болт",
                requested_quantity=Decimal("10"),
                unit="шт",
                warehouse_id="wh-main",
            )
        ],
        supplies=[
            PickerSupplyItem(
                supply_id="s1",
                source_type="warehouse",
                nomenclature_id="mat",
                quantity=Decimal("15"),
                warehouse_id="wh-main",
                accounting_quantity=Decimal("15"),
                factual_quantity=Decimal("15"),
            )
        ],
        calculated_at=datetime(2026, 7, 23, tzinfo=UTC),
    )
    assert result.decision_kind == "stock_confirmation"
    assert result.positions[0].outcome is PickerOutcome.FULLY_AVAILABLE
    assert result.conclusion["quantity_to_issue"] == "10"
    assert result.conclusion["confirmed_deficit"] == "0"


def test_other_warehouse_stock_is_excluded():
    result = calculate_picker_assessment(
        case=PickerCaseInput(
            case_id="c1",
            case_number="НП-1",
            source_1c_ref="ref",
            warehouse_1c_ref="wh-main",
        ),
        needs=[
            PickerNeedLine(
                line_id="1",
                nomenclature_id="mat",
                nomenclature_name="Гайка",
                requested_quantity=Decimal("8"),
                warehouse_id="wh-main",
            )
        ],
        supplies=[
            PickerSupplyItem(
                supply_id="s1",
                source_type="warehouse",
                nomenclature_id="mat",
                quantity=Decimal("20"),
                warehouse_id="wh-other",
            )
        ],
        calculated_at=datetime(2026, 7, 23, tzinfo=UTC),
    )
    assert result.positions[0].available_for_issue == Decimal("0")
    assert result.positions[0].confirmed_deficit == Decimal("8")
    assert result.positions[0].excluded_supply[0]["reason"] == "other_warehouse"


def test_foreign_assignment_is_not_available():
    result = calculate_picker_assessment(
        case=PickerCaseInput(
            case_id="c1",
            case_number="НП-1",
            source_1c_ref="ref",
            warehouse_1c_ref="wh-main",
        ),
        needs=[
            PickerNeedLine(
                line_id="1",
                nomenclature_id="mat",
                nomenclature_name="Корпус",
                requested_quantity=Decimal("5"),
                warehouse_id="wh-main",
                assignment_id="asn-this",
            )
        ],
        supplies=[
            PickerSupplyItem(
                supply_id="s-free",
                source_type="warehouse",
                nomenclature_id="mat",
                quantity=Decimal("2"),
                warehouse_id="wh-main",
            ),
            PickerSupplyItem(
                supply_id="s-other",
                source_type="warehouse",
                nomenclature_id="mat",
                quantity=Decimal("10"),
                warehouse_id="wh-main",
                assignment_id="asn-other",
                assignment_name="Чужой заказ",
            ),
            PickerSupplyItem(
                supply_id="s-own",
                source_type="warehouse",
                nomenclature_id="mat",
                quantity=Decimal("3"),
                warehouse_id="wh-main",
                assignment_id="asn-this",
                assignment_name="Этот заказ",
            ),
        ],
        calculated_at=datetime(2026, 7, 23, tzinfo=UTC),
    )
    line = result.positions[0]
    assert line.available_for_issue == Decimal("5")
    assert line.quantity_to_issue == Decimal("5")
    assert line.reserved_other_quantity == Decimal("10")
    assert line.outcome is PickerOutcome.FULLY_AVAILABLE


def test_deficit_when_store_room_empty():
    result = calculate_picker_assessment(
        case=PickerCaseInput(
            case_id="c1",
            case_number="НП-1",
            source_1c_ref="ref",
        ),
        needs=[
            PickerNeedLine(
                line_id="1",
                nomenclature_id="mat",
                nomenclature_name="Гайка",
                requested_quantity=Decimal("8"),
            )
        ],
        supplies=[],
        calculated_at=datetime(2026, 7, 23, tzinfo=UTC),
    )
    assert result.decision_kind == "deficit_confirmation"
    assert result.positions[0].confirmed_deficit == Decimal("8")
    assert result.conclusion["quantity_to_purchase"] == "8"


def test_soft_warehouse_reserve_reduces_available():
    """Physical stock on warehouse minus РезервироватьНаСкладе => Доступно like in 1C."""
    result = calculate_picker_assessment(
        case=PickerCaseInput(
            case_id="c1",
            case_number="НП00-001343",
            source_1c_ref="89a60dde-7f5d-11f1-9841-6cb31113810e",
            warehouse_1c_ref="wh-main",
        ),
        needs=[
            PickerNeedLine(
                line_id="9",
                nomenclature_id="tdc",
                nomenclature_name="Плата печатная TDC-1400",
                requested_quantity=Decimal("10"),
                warehouse_id="wh-main",
            )
        ],
        supplies=[
            PickerSupplyItem(
                supply_id="physical",
                source_type="warehouse",
                nomenclature_id="tdc",
                quantity=Decimal("10"),
                warehouse_id="wh-main",
            ),
            PickerSupplyItem(
                supply_id="reserve",
                source_type="reservation",
                nomenclature_id="tdc",
                quantity=Decimal("10"),
                warehouse_id="wh-main",
                reserved_for_other=True,
            ),
        ],
        calculated_at=datetime(2026, 7, 23, tzinfo=UTC),
    )
    line = result.positions[0]
    assert line.warehouse_stock == Decimal("10")
    assert line.available_for_issue == Decimal("0")
    assert line.confirmed_deficit == Decimal("10")
    assert line.reserved_other_quantity == Decimal("10")
    assert line.outcome is PickerOutcome.DEFICIT_CONFIRMED


def test_discrepancy_blocks_issue():
    result = calculate_picker_assessment(
        case=PickerCaseInput(
            case_id="c1",
            case_number="НП-1",
            source_1c_ref="ref",
            warehouse_1c_ref="wh-main",
        ),
        needs=[
            PickerNeedLine(
                line_id="1",
                nomenclature_id="mat",
                nomenclature_name="Шайба",
                requested_quantity=Decimal("5"),
                warehouse_id="wh-main",
            )
        ],
        supplies=[
            PickerSupplyItem(
                supply_id="s1",
                source_type="store_room",
                nomenclature_id="mat",
                quantity=Decimal("2"),
                warehouse_id="wh-main",
                accounting_quantity=Decimal("10"),
                factual_quantity=Decimal("2"),
            )
        ],
        calculated_at=datetime(2026, 7, 23, tzinfo=UTC),
    )
    assert result.decision_kind == "discrepancy_return"
    assert result.positions[0].outcome is PickerOutcome.DISCREPANCY_RETURN
