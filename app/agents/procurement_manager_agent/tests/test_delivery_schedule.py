"""Unit tests for delivery schedule formula and fulfillment status."""

from datetime import date

from app.agents.procurement_manager_agent.delivery_schedule import (
    compute_schedule,
    offer_meets_deadline,
    planned_arrival,
)
from app.agents.procurement_manager_agent.fulfillment import derive_fulfillment_status


def test_planned_arrival_from_lead_days() -> None:
    assert planned_arrival(supplier_lead_days=5, as_of=date(2026, 7, 1)) == date(2026, 7, 6)


def test_planned_arrival_prefers_ship_date() -> None:
    assert planned_arrival(
        supplier_lead_days=10,
        supplier_ship_date=date(2026, 8, 1),
        as_of=date(2026, 7, 1),
    ) == date(2026, 8, 1)


def test_offer_meets_deadline() -> None:
    assert offer_meets_deadline(date(2026, 7, 10), 3, today=date(2026, 7, 1)) is True
    assert offer_meets_deadline(date(2026, 7, 2), 5, today=date(2026, 7, 1)) is False
    assert offer_meets_deadline(date(2026, 7, 10), None, today=date(2026, 7, 1)) is None


def test_compute_schedule_formula() -> None:
    result = compute_schedule(
        required_date=date(2026, 7, 20),
        lead_days=7,
        as_of=date(2026, 7, 10),
    )
    assert result["planned_arrival"] == "2026-07-17"
    assert result["meets_deadline"] is True
    assert "planned_arrival" in result["formula"]


def test_derive_fulfillment_no_supplier() -> None:
    assert (
        derive_fulfillment_status(case_status="purchase_draft", workspace={})
        == "no_supplier"
    )


def test_derive_fulfillment_from_case_status() -> None:
    assert derive_fulfillment_status(case_status="payment_pending") == "payment"
    assert derive_fulfillment_status(case_status="in_transit") == "delivery"
    assert derive_fulfillment_status(case_status="receiving") == "otk_presentation"
    assert derive_fulfillment_status(case_status="posted") == "completed"
