from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.agents.production_preparation_engineer_agent.calculator import (
    calculate_engineer_assessment,
    select_resource_specification,
)
from app.agents.production_preparation_engineer_agent.schemas import (
    EngineerCaseInput,
    EngineerCriticality,
    EngineerNeedLine,
    EngineerOutcome,
    EngineerSupplyItem,
    ResourceSpecification,
    ResourceSpecificationMaterial,
)

NOW = datetime(2026, 7, 20, tzinfo=UTC)
REQUIRED = datetime(2026, 8, 7, tzinfo=UTC)


def _case() -> EngineerCaseInput:
    return EngineerCaseInput(
        case_id="case-1",
        case_number="НП00-1",
        source_1c_ref="source-ref",
        source_status="КВыполнению",
        warehouse_1c_ref="warehouse-main",
        required_date=REQUIRED,
        production_order_1c_ref="production-order",
    )


def _need(**updates) -> EngineerNeedLine:
    values = {
        "line_id": "product-1",
        "nomenclature_id": "product",
        "nomenclature_name": "Прибор",
        "unit": "шт",
        "product_quantity": "100",
        "required_date": REQUIRED,
        "acceptable_analog_available": False,
    }
    values.update(updates)
    return EngineerNeedLine.model_validate(values)


def _spec(**updates) -> ResourceSpecification:
    values = {
        "specification_id": "spec-1",
        "name": "РС Прибор",
        "version": "2",
        "status": "Действует",
        "approved": True,
        "product_id": "product",
        "completeness_score": 10,
        "materials": [
            ResourceSpecificationMaterial(
                line_id="material-1",
                nomenclature_id="steel",
                nomenclature_name="Сталь 20 лист 5 мм",
                unit="кг",
                consumption_rate=Decimal("5"),
                technological_loss_percent=Decimal("3"),
                production_stage_id="stage-1",
                production_stage_name="Сборка",
            )
        ],
    }
    values.update(updates)
    return ResourceSpecification.model_validate(values)


def test_selects_most_complete_active_specification():
    selected = select_resource_specification(
        _need(),
        [
            _spec(specification_id="less", completeness_score=2),
            _spec(specification_id="best", completeness_score=20),
            _spec(specification_id="inactive", status="Закрыта", completeness_score=100),
        ],
        REQUIRED,
    )
    assert selected is not None
    assert selected.specification_id == "best"


def test_calculates_gross_net_and_excludes_foreign_reserve():
    result = calculate_engineer_assessment(
        case=_case(),
        needs=[_need()],
        specifications=[_spec()],
        supplies=[
            EngineerSupplyItem(
                supply_id="stock",
                source_type="warehouse",
                nomenclature_id="steel",
                unit="кг",
                quantity=Decimal("200"),
                warehouse_id="warehouse-main",
            ),
            EngineerSupplyItem(
                supply_id="arrival",
                source_type="supplier_order",
                nomenclature_id="steel",
                unit="кг",
                quantity=Decimal("100"),
                available_at=datetime(2026, 8, 1, tzinfo=UTC),
                linked_document_number="ЗП-00541",
            ),
            EngineerSupplyItem(
                supply_id="foreign-reserve",
                source_type="reservation",
                nomenclature_id="steel",
                unit="кг",
                quantity=Decimal("50"),
                reserved_for_other=True,
            ),
        ],
        calculated_at=NOW,
    )
    line = result.positions[0]
    assert line.gross_requirement == Decimal("515")
    assert line.total_available_supply == Decimal("300")
    assert line.net_requirement == Decimal("215")
    assert line.outcome is EngineerOutcome.PARTIALLY_COVERED
    assert {item.reason for item in line.excluded_supply} == {"reserved_for_other_order"}


def test_open_order_cover_does_not_request_new_procurement():
    result = calculate_engineer_assessment(
        case=_case(),
        needs=[_need(product_quantity="10")],
        specifications=[_spec()],
        supplies=[
            EngineerSupplyItem(
                supply_id="order",
                source_type="supplier_order",
                nomenclature_id="steel",
                unit="кг",
                quantity=Decimal("60"),
                available_at=datetime(2026, 8, 1, tzinfo=UTC),
                linked_document_number="ЗП-00541",
            )
        ],
        calculated_at=NOW,
    )
    line = result.positions[0]
    assert line.net_requirement == 0
    assert line.outcome is EngineerOutcome.COVERED_BY_OPEN_ORDER
    assert "новую закупку не создавать" in line.recommendation.lower()


def test_marks_confirmed_production_impact_as_critical():
    result = calculate_engineer_assessment(
        case=_case(),
        needs=[
            _need(
                stage_cannot_start_without_material=True,
                section_stop_risk=True,
            )
        ],
        specifications=[_spec()],
        supplies=[],
        calculated_at=NOW,
    )
    line = result.positions[0]
    assert line.criticality is EngineerCriticality.CRITICAL
    assert line.outcome is EngineerOutcome.CRITICAL_SHORTAGE
    assert line.critical_impact is not None


def test_missing_specification_returns_addressed_issue():
    result = calculate_engineer_assessment(
        case=_case(),
        needs=[_need()],
        specifications=[],
        supplies=[],
        calculated_at=NOW,
    )
    assert not result.positions
    assert result.validation_issues[0].code == "active_specification_missing"
    assert "Прибор" in result.validation_issues[0].message
