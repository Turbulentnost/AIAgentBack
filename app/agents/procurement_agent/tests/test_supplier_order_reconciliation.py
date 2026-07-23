from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.procurement_role_agents.config import (
    PURCHASE_MANAGER_AGENT_ID,
    WAREHOUSE_PICKER_AGENT_ID,
)
from app.models.enums import ProcurementCaseStatus, ProcurementSourceType
from app.models.procurement import ProcurementCase, ProcurementCasePosition
from app.services.supplier_order_reconciliation_service import (
    SupplierOrderReconciliationService,
)


def _case() -> ProcurementCase:
    case = ProcurementCase(
        id=uuid.uuid4(),
        correlation_id="supplier-reconciliation-test",
        source_type=ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value,
        source_1c_ref="11111111-1111-1111-1111-111111111111",
        source_database="test",
        source_number="НП00-001356",
        status=ProcurementCaseStatus.AGENT_WAITING.value,
        assigned_agents=[WAREHOUSE_PICKER_AGENT_ID],
        current_agent_id=WAREHOUSE_PICKER_AGENT_ID,
        requested_operation="assess_need",
        idempotency_key="supplier-reconciliation-test",
        case_metadata={"picker_invoked_at": datetime.now(UTC).isoformat()},
    )
    case.positions = [
        ProcurementCasePosition(
            id=uuid.uuid4(),
            case_id=case.id,
            line_id="1",
            line_number=1,
            nomenclature_id="nom-1",
            nomenclature_name="Позиция 1",
            quantity=Decimal("100"),
            cancelled=False,
        ),
        ProcurementCasePosition(
            id=uuid.uuid4(),
            case_id=case.id,
            line_id="2",
            line_number=2,
            nomenclature_id="nom-2",
            nomenclature_name="Позиция 2",
            quantity=Decimal("200"),
            cancelled=False,
        ),
    ]
    case.supplier_order_links = []
    return case


def _order(*nomenclature_refs: str) -> dict:
    return {
        "ref": "22222222-2222-2222-2222-222222222222",
        "number": "НП00-004977",
        "date": "2026-07-20T00:00:00Z",
        "status": "КВыполнению",
        "basis": {
            "ref": "11111111-1111-1111-1111-111111111111",
            "type": "Document_ЗаказМатериаловВПроизводство",
        },
        "basisResolution": {
            "status": "resolved",
            "sourceRef": "11111111-1111-1111-1111-111111111111",
            "sourceType": "production_material_order",
            "chain": [],
        },
        "lines": [
            {
                "lineNumber": index,
                "nomenclatureRef": nomenclature_ref,
                # Coverage is presence-only: even a tiny quantity covers the position.
                "quantity": 0.001,
                "cancelled": False,
            }
            for index, nomenclature_ref in enumerate(nomenclature_refs, start=1)
        ],
    }


@pytest.mark.asyncio
async def test_partial_presence_coverage_keeps_both_workspaces() -> None:
    db = MagicMock()
    db.flush = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.get = AsyncMock(return_value=None)
    case = _case()
    service = SupplierOrderReconciliationService(db, mcp_client=MagicMock())

    changed = await service._apply_case(case, [_order("nom-1")])

    assert changed is True
    assert case.status == ProcurementCaseStatus.AGENT_WAITING.value
    assert set(case.assigned_agents or []) == {
        WAREHOUSE_PICKER_AGENT_ID,
        PURCHASE_MANAGER_AGENT_ID,
    }
    snapshot = (case.case_metadata or {})["supplier_order_coverage"]
    assert snapshot["coverage_status"] == "partial"
    assert snapshot["covered_positions"] == 1
    assert snapshot["positions"][0]["purchasing"] is True
    assert snapshot["positions"][1]["purchasing"] is False


@pytest.mark.asyncio
async def test_full_presence_coverage_closes_picker_and_is_idempotent() -> None:
    db = MagicMock()
    db.flush = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.get = AsyncMock(return_value=None)
    case = _case()
    service = SupplierOrderReconciliationService(db, mcp_client=MagicMock())
    order = _order("nom-1", "nom-2")

    first = await service._apply_case(case, [order])
    second = await service._apply_case(case, [order])

    assert first is True
    assert second is False
    assert case.status == ProcurementCaseStatus.ORDERED.value
    assert case.current_agent_id == PURCHASE_MANAGER_AGENT_ID
    assert case.assigned_agents == [PURCHASE_MANAGER_AGENT_ID]
    assert (case.case_metadata or {})["picker_workspace_status"] == "archived"
    assert (case.case_metadata or {}).get("picker_workspace_archived_at")
    assert (case.case_metadata or {})["picker_auto_archived_reason"] == (
        "all_positions_in_supplier_orders"
    )
    assert (case.case_metadata or {})["picker_procurement_status"] == "covered"
    assert (case.case_metadata or {})["supplier_order_coverage"]["coverage_status"] == "full"


@pytest.mark.asyncio
async def test_cancelled_supplier_line_does_not_cover_position() -> None:
    db = MagicMock()
    db.flush = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.get = AsyncMock(return_value=None)
    case = _case()
    service = SupplierOrderReconciliationService(db, mcp_client=MagicMock())
    order = _order("nom-1")
    order["lines"][0]["cancelled"] = True

    await service._apply_case(case, [order])

    snapshot = (case.case_metadata or {})["supplier_order_coverage"]
    assert snapshot["coverage_status"] == "none"
    assert snapshot["covered_positions"] == 0
    assert case.assigned_agents == [WAREHOUSE_PICKER_AGENT_ID]
