from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.agents.procurement_role_agents.config import (
    PURCHASE_MANAGER_AGENT_ID,
    QUALITY_ENGINEER_AGENT_ID,
    WAREHOUSE_PICKER_AGENT_ID,
)
from app.models.enums import ProcurementCaseStatus, ProcurementSourceType
from app.models.procurement import (
    ProcurementCase,
    ProcurementCasePosition,
    ProcurementSupplierOrderLink,
)
from app.services.tmc_presentation_reconciliation_service import (
    TmcPresentationReconciliationService,
    build_otk_presentations_for_case,
)


def _case(*, coverage_status: str = "full") -> ProcurementCase:
    case = ProcurementCase(
        id=uuid.uuid4(),
        correlation_id="tmc-reconciliation-test",
        source_type=ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value,
        source_1c_ref="11111111-1111-1111-1111-111111111111",
        source_database="test",
        source_number="НП00-001083",
        status=ProcurementCaseStatus.ORDERED.value,
        assigned_agents=[PURCHASE_MANAGER_AGENT_ID],
        current_agent_id=PURCHASE_MANAGER_AGENT_ID,
        requested_operation="monitor_supplier_orders",
        idempotency_key="tmc-reconciliation-test",
        case_metadata={
            "purchase_manager_invoked_at": datetime.now(UTC).isoformat(),
            "purchase_manager_workspace_status": "awaiting_action",
            "supplier_order_coverage": {
                "coverage_status": coverage_status,
                "positions": [
                    {
                        "nomenclature_id": "nom-1",
                        "nomenclature_name": "Позиция 1",
                        "purchasing": True,
                    }
                ],
            },
        },
    )
    case.positions = [
        ProcurementCasePosition(
            id=uuid.uuid4(),
            case_id=case.id,
            line_id="1",
            line_number=1,
            nomenclature_id="nom-1",
            nomenclature_name="Позиция 1",
            quantity=Decimal("10"),
            cancelled=False,
        )
    ]
    now = datetime.now(UTC)
    case.supplier_order_links = [
        ProcurementSupplierOrderLink(
            id=uuid.uuid4(),
            case_id=case.id,
            supplier_order_1c_ref="22222222-2222-2222-2222-222222222222",
            supplier_order_number="НП00-003046",
            first_detected_at=now,
            last_seen_at=now,
            lines=[],
        ),
        ProcurementSupplierOrderLink(
            id=uuid.uuid4(),
            case_id=case.id,
            supplier_order_1c_ref="33333333-3333-3333-3333-333333333333",
            supplier_order_number="НП00-004066",
            first_detected_at=now,
            last_seen_at=now,
            lines=[],
        ),
    ]
    return case


def _journal(order_ref: str, number: str) -> dict:
    return {
        "ref": str(uuid.uuid4()),
        "number": number,
        "date": "2026-07-21T16:01:29",
        "status": "Исполнен",
        "documentStage": "Закрыт",
        "invoiceNumber": "УПД-1",
        "invoiceDate": "2026-07-17T00:00:00",
        "dueAt": "2026-07-24T23:59:00",
        "storageZone": "пп-1-2",
        "presentationPlace": "пп-1-2",
        "supplierName": "Поставщик",
        "supplierOrderRef": order_ref,
        "basis": {
            "ref": order_ref,
            "type": "StandardODATA.Document_ЗаказПоставщику",
        },
        "lines": [
            {
                "nomenclatureRef": "nom-1",
                "quantity": 10,
                "qtyUpd": 10,
                "qtyFact": 10,
            }
        ],
    }


@pytest.mark.asyncio
async def test_partial_journal_starts_otk_keeps_purchase_manager() -> None:
    case = _case()
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    svc = TmcPresentationReconciliationService(db, enqueue_case=False)
    journal = {
        "22222222-2222-2222-2222-222222222222": _journal(
            "22222222-2222-2222-2222-222222222222", "000026462"
        )
    }
    # Avoid event insert path looking up DB.
    svc._append_event = AsyncMock()  # type: ignore[method-assign]
    result = await svc._apply_case(case, journal)
    assert result["handed_off"] is False
    assert case.case_metadata["tmc_presentation_coverage"]["status"] == "partial"
    assert case.case_metadata.get("otk_started_at")
    assert QUALITY_ENGINEER_AGENT_ID in (case.assigned_agents or [])
    assert PURCHASE_MANAGER_AGENT_ID in (case.assigned_agents or [])
    assert case.current_agent_id == PURCHASE_MANAGER_AGENT_ID
    assert len(case.case_metadata["otk_presentations"]) == 1


@pytest.mark.asyncio
async def test_full_journal_handoff_to_otk_keeps_picker_on_partial_coverage() -> None:
    case = _case(coverage_status="partial")
    case.assigned_agents = [WAREHOUSE_PICKER_AGENT_ID, PURCHASE_MANAGER_AGENT_ID]
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    svc = TmcPresentationReconciliationService(db, enqueue_case=False)
    svc._append_event = AsyncMock()  # type: ignore[method-assign]
    journal = {
        "22222222-2222-2222-2222-222222222222": _journal(
            "22222222-2222-2222-2222-222222222222", "000026462"
        ),
        "33333333-3333-3333-3333-333333333333": _journal(
            "33333333-3333-3333-3333-333333333333", "000026463"
        ),
    }
    result = await svc._apply_case(case, journal)
    assert result["handed_off"] is True
    assert case.current_agent_id == QUALITY_ENGINEER_AGENT_ID
    assert case.status == ProcurementCaseStatus.QUALITY_ASSIGNED.value
    assert case.control_point == "quality"
    assert case.case_metadata["purchase_manager_workspace_status"] == "archived"
    assert WAREHOUSE_PICKER_AGENT_ID in (case.assigned_agents or [])
    assert PURCHASE_MANAGER_AGENT_ID not in (case.assigned_agents or [])
    assert QUALITY_ENGINEER_AGENT_ID in (case.assigned_agents or [])
    assert len(case.case_metadata["otk_presentations"]) == 2


def test_build_otk_presentations_maps_journal_fields() -> None:
    case = _case()
    journal = {
        "22222222-2222-2222-2222-222222222222": _journal(
            "22222222-2222-2222-2222-222222222222", "000026462"
        )
    }
    # Only one order in journal map → one card for that link.
    case.supplier_order_links = case.supplier_order_links[:1]
    cards = build_otk_presentations_for_case(case, journal_by_order_ref=journal)
    assert len(cards) == 1
    assert cards[0]["purchase_order"] == "НП00-003046"
    assert cards[0]["invoice_number"] == "УПД-1"
    assert cards[0]["lines"][0]["nomenclature"] == "Позиция 1"
    assert cards[0]["case_id"] == str(case.id)
