from __future__ import annotations

from decimal import Decimal

from app.agents.procurement_agent.coverage import calculate_coverage
from app.agents.procurement_agent.schemas import ProcurementNeedPosition, ProcurementSupplyItem


def _position(**overrides):
    values = {
        "line_id": "line-1",
        "nomenclature_id": "item-1",
        "nomenclature_name": "Материал 1",
        "unit": "кг",
        "gross_quantity": Decimal("10"),
    }
    values.update(overrides)
    return ProcurementNeedPosition(**values)


def _supply(**overrides):
    values = {
        "supply_id": "supply-1",
        "source_type": "warehouse",
        "nomenclature_id": "item-1",
        "unit": "кг",
        "quantity": Decimal("10"),
        "evidence_id": "evidence-1",
    }
    values.update(overrides)
    return ProcurementSupplyItem(**values)


def _calculate(*, position=None, supplies=None, issues=None):
    return calculate_coverage(
        case_id="case-1",
        source_basis={"source_1c_ref": "ref-1"},
        positions=[position or _position()],
        supplies=supplies or [],
        evidence_ids=["evidence-1"],
        data_issues=issues or [],
    )


def test_fully_covered_requirement() -> None:
    result = _calculate(supplies=[_supply()])
    assert result.status == "covered"
    assert result.positions[0].net_requirement == 0


def test_partially_covered_requirement() -> None:
    result = _calculate(supplies=[_supply(quantity=Decimal("4"))])
    assert result.status == "partially_covered"
    assert result.positions[0].net_requirement == Decimal("6")


def test_fully_uncovered_requirement() -> None:
    result = _calculate()
    assert result.status == "uncovered"
    assert result.positions[0].net_requirement == Decimal("10")


def test_supply_greater_than_requirement_caps_net_at_zero() -> None:
    result = _calculate(supplies=[_supply(quantity=Decimal("14"))])
    assert result.status == "covered"
    assert result.positions[0].net_requirement == 0


def test_reservation_for_other_need_is_excluded() -> None:
    result = _calculate(supplies=[_supply(reserved_for_other=True)])
    assert result.positions[0].available_supply == 0
    assert result.positions[0].excluded_supply[0].reason == "reserved_for_other_need"


def test_quarantine_and_defect_are_excluded() -> None:
    result = _calculate(
        supplies=[
            _supply(supply_id="q", quarantine=True),
            _supply(supply_id="d", defective=True),
        ]
    )
    assert {item.reason for item in result.positions[0].excluded_supply} == {
        "quarantine",
        "defective",
    }


def test_unconfirmed_supplier_order_is_excluded() -> None:
    result = _calculate(
        supplies=[_supply(source_type="supplier_order", confirmed=False)]
    )
    assert result.positions[0].excluded_supply[0].reason == "unconfirmed"


def test_incoming_control_not_passed_is_excluded() -> None:
    result = _calculate(supplies=[_supply(incoming_control_passed=False)])
    assert result.positions[0].excluded_supply[0].reason == "incoming_control_not_passed"


def test_duplicate_supply_is_counted_once() -> None:
    result = _calculate(
        supplies=[
            _supply(source_type="warehouse"),
            _supply(source_type="in_transit"),
        ]
    )
    assert result.positions[0].available_supply == Decimal("10")


def test_conflicting_duplicate_supply_requires_human() -> None:
    result = _calculate(
        supplies=[
            _supply(source_type="warehouse", quantity=Decimal("10")),
            _supply(source_type="in_transit", quantity=Decimal("8")),
        ]
    )
    assert result.status == "data_insufficient"
    assert result.human_action_required is not None


def test_incompatible_units_require_human() -> None:
    result = _calculate(supplies=[_supply(unit="шт")])
    assert result.status == "data_insufficient"
    assert result.positions[0].excluded_supply[0].reason == "unit_mismatch"


def test_ambiguous_nomenclature_requires_human() -> None:
    result = _calculate(position=_position(match_status="ambiguous"))
    assert result.status == "data_insufficient"


def test_stale_or_unavailable_evidence_prevents_covered_status() -> None:
    stale = _calculate(supplies=[_supply()], issues=["Доказательства устарели."])
    unavailable = _calculate(
        supplies=[_supply()],
        issues=["Обязательная возможность MCP недоступна."],
    )
    assert stale.status == "data_insufficient"
    assert unavailable.status == "data_insufficient"
