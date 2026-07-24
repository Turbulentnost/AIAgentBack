from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agents.procurement_manager_agent.material_bank import reset_material_bank_for_tests
from app.agents.procurement_manager_agent.schemas import (
    AgentResumeRequest,
    AgentRunRequest,
    ApprovalRecord,
    PurchaseOrderDraftRequest,
    PurchaseOrderLine,
    Supplier,
    SupplierSearchRequest,
    SupplierSearchResult,
)
from app.agents.procurement_manager_agent.service import ProcurementManagerService
from app.models.enums import ProcurementCaseStatus


class FakeSearch:
    internal_threshold = 1

    async def search(self, request: SupplierSearchRequest) -> SupplierSearchResult:
        return await self.search_internal(request)

    async def search_internal(self, request: SupplierSearchRequest) -> SupplierSearchResult:
        return SupplierSearchResult(
            query=request.query or "steel",
            suppliers=[
                Supplier(supplier_id="s-1", name="Supplier One"),
                Supplier(supplier_id="s-2", name="Supplier Two"),
            ],
            sources_used=["internal"],
        )

    async def search_web(self, request: SupplierSearchRequest) -> SupplierSearchResult:
        return SupplierSearchResult(
            query=request.query or "steel",
            suppliers=[],
            sources_used=["web"],
            web_fallback_used=True,
        )


class FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def scalar(self, _stmt: object) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> None:
        return None


def _case(*, with_positions: bool = True) -> SimpleNamespace:
    positions = []
    if with_positions:
        positions = [
            SimpleNamespace(
                line_id="line-steel",
                nomenclature_id="steel",
                nomenclature_name="Сталь",
                quantity=Decimal("10"),
                unit="кг",
                cancelled=False,
                required_date=None,
                raw_payload={},
            )
        ]
    return SimpleNamespace(
        id=uuid4(),
        source_number="REQ-AGENT-1",
        required_date=None,
        status=ProcurementCaseStatus.PURCHASE_DRAFT.value,
        control_point="purchase",
        correlation_id="corr-1",
        case_metadata={"procurement_manager": {}},
        positions=positions,
        current_task_id=None,
        current_agent_id="procurement_logistics_agent",
        assigned_agents=["procurement_logistics_agent"],
        requested_operation="route_confirmed_deficit",
    )


@pytest.mark.asyncio
async def test_agent_run_resume_creates_po_draft_without_payment() -> None:
    reset_material_bank_for_tests()
    case = _case()
    service = ProcurementManagerService(FakeDb(), supplier_search=FakeSearch())  # type: ignore[arg-type]
    service.require_case = lambda _case_id: _async_value(case)  # type: ignore[method-assign]
    service._case = lambda _case_id: _async_value(case)  # type: ignore[method-assign]

    status = await service.agent_run(
        case.id,
        AgentRunRequest(idempotency_key="run-1", allow_web_fallback=False),
    )
    assert status.paused_for_human is True
    assert status.interrupt_type == "procurement_shortlist_approval"
    assert status.payment_execution_allowed is False
    assert status.evaluation is not None

    status = await service.agent_resume(
        case.id,
        AgentResumeRequest(action="approve_shortlist", idempotency_key="resume-shortlist"),
    )
    assert status.paused_for_human is True
    assert status.interrupt_type == "procurement_order_approval"
    assert status.purchase_order_draft is not None
    assert status.purchase_order_draft["payment_execution_allowed"] is False
    assert status.purchase_order_draft["status"] == "draft"

    status = await service.agent_resume(
        case.id,
        AgentResumeRequest(action="approve_order_draft", idempotency_key="resume-order"),
    )
    assert status.paused_for_human is False
    assert status.status == "order_draft_approved"
    drafts = await service.list_purchase_order_drafts(case.id)
    assert drafts
    assert drafts[-1].payment_execution_allowed is False
    meta = case.case_metadata["procurement_manager"]
    stored = meta["purchase_order_drafts"][-1]
    assert stored["executed"] is False
    assert stored["draft"]["payment_execution_allowed"] is False
    # PO draft lines must map into position-row line_amounts (price + sum).
    line_amounts = meta.get("line_amounts") or {}
    assert "line-steel" in line_amounts
    assert Decimal(str(line_amounts["line-steel"]["unit_price"])) > 0
    assert Decimal(str(line_amounts["line-steel"]["amount"])) > 0


def test_sync_line_amounts_from_po_drafts_heals_missing_prices() -> None:
    workspace = {
        "purchase_order_drafts": [
            {
                "draft": {
                    "po_id": "po-1",
                    "supplier_id": "sup-1",
                    "supplier_name": "ООО Тест",
                    "currency": "RUB",
                    "lines": [
                        {
                            "line_id": "pm-25-L2",
                            "nomenclature_id": "missing-seal-kit",
                            "quantity": "22",
                            "unit_price": "15.44",
                        }
                    ],
                }
            }
        ],
        "line_amounts": {},
    }
    changed = ProcurementManagerService._sync_line_amounts_from_po_drafts(workspace)
    assert changed is True
    entry = workspace["line_amounts"]["pm-25-L2"]
    assert Decimal(str(entry["unit_price"])) == Decimal("15.44")
    assert Decimal(str(entry["amount"])) == Decimal("339.68")
    # Positive manual price must not be overwritten.
    workspace["line_amounts"]["pm-25-L2"] = {
        "line_id": "pm-25-L2",
        "unit_price": "20",
        "amount": "440",
        "currency": "RUB",
    }
    assert ProcurementManagerService._sync_line_amounts_from_po_drafts(workspace) is False
    assert workspace["line_amounts"]["pm-25-L2"]["unit_price"] == "20"


@pytest.mark.asyncio
async def test_agent_resume_rehydrates_after_memory_saver_loss() -> None:
    """Backend restart drops MemorySaver; HITL must resume from workspace snapshot."""
    reset_material_bank_for_tests()
    case = _case()
    service = ProcurementManagerService(FakeDb(), supplier_search=FakeSearch())  # type: ignore[arg-type]
    service.require_case = lambda _case_id: _async_value(case)  # type: ignore[method-assign]
    service._case = lambda _case_id: _async_value(case)  # type: ignore[method-assign]

    status = await service.agent_run(
        case.id,
        AgentRunRequest(idempotency_key="run-lost-ckpt", allow_web_fallback=False),
    )
    assert status.paused_for_human is True
    assert status.interrupt_type == "procurement_shortlist_approval"

    import app.agents.procurement_manager_agent.service as service_module
    from app.agents.procurement_manager_agent.graph import build_graph
    from langgraph.checkpoint.memory import MemorySaver

    previous = service_module._runtime_graph
    service_module._runtime_graph = build_graph(checkpointer=MemorySaver())
    try:
        status = await service.agent_resume(
            case.id,
            AgentResumeRequest(
                action="approve_shortlist",
                idempotency_key="resume-shortlist-after-restart",
            ),
        )
        assert status.paused_for_human is True
        assert status.interrupt_type == "procurement_order_approval"
        assert status.purchase_order_draft is not None

        # Simulate another restart while paused on order approval.
        service_module._runtime_graph = build_graph(checkpointer=MemorySaver())
        status = await service.agent_resume(
            case.id,
            AgentResumeRequest(
                action="approve_order_draft",
                idempotency_key="resume-order-after-restart",
            ),
        )
        assert status.paused_for_human is False
        assert status.status == "order_draft_approved"
    finally:
        service_module._runtime_graph = previous


@pytest.mark.asyncio
async def test_po_draft_create_requires_approval_for_approved_status() -> None:
    case = _case(with_positions=False)
    case.case_metadata = {
        "procurement_manager": {
            "approvals": [
                {
                    "idempotency_key": "appr-1",
                    "approval": ApprovalRecord(
                        approval_id="appr-1",
                        operation="create_supplier_order",
                        status="approved",
                        created_at=datetime.now(UTC),
                    ).model_dump(mode="json"),
                }
            ],
            "suppliers": [{"supplier_id": "s-1", "name": "Supplier One"}],
            "purchase_order_drafts": [],
        }
    }
    service = ProcurementManagerService(FakeDb(), supplier_search=FakeSearch())  # type: ignore[arg-type]
    service.require_case = lambda _case_id: _async_value(case)  # type: ignore[method-assign]

    draft = await service.create_purchase_order_draft(
        case.id,
        PurchaseOrderDraftRequest(
            supplier_id="s-1",
            lines=[
                PurchaseOrderLine(
                    line_id="l1",
                    nomenclature_id="steel",
                    description="Сталь",
                    quantity=Decimal("2"),
                    unit_price=Decimal("100"),
                )
            ],
            idempotency_key="po-1",
        ),
        approval_id="appr-1",
    )
    assert draft.status == "approved_draft"
    assert draft.payment_execution_allowed is False
    stored = case.case_metadata["procurement_manager"]["purchase_order_drafts"][0]
    assert stored["executed"] is False


@pytest.mark.asyncio
async def test_orchestrator_handoff_starts_agent_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procurement_orchestrator_service import ProcurementOrchestratorService

    case = _case()
    calls: list[str] = []

    class StubManager:
        def __init__(self, _db: object) -> None:
            pass

        async def agent_run(self, case_id: object, request: object) -> SimpleNamespace:
            calls.append(str(case_id))
            meta = case.case_metadata.setdefault("procurement_manager", {})
            meta["lifecycle_state"] = "approval_required"
            meta["agent_stage"] = "await_supplier_hitl"
            meta["agent_run_idempotency_key"] = getattr(request, "idempotency_key", "x")
            return SimpleNamespace(
                stage="await_supplier_hitl",
                paused_for_human=True,
                payment_execution_allowed=False,
            )

    monkeypatch.setattr(
        "app.agents.procurement_manager_agent.service.ProcurementManagerService",
        StubManager,
    )
    orch = ProcurementOrchestratorService(FakeDb(), enqueue_case=False)  # type: ignore[arg-type]
    await orch._ensure_procurement_manager_agent_running(case)  # noqa: SLF001
    assert calls
    assert case.case_metadata["procurement_manager"]["lifecycle_state"] == "approval_required"


def test_save_workspace_json_encodes_decimals() -> None:
    case = _case()
    service = ProcurementManagerService(FakeDb())  # type: ignore[arg-type]
    service._save_workspace(  # noqa: SLF001
        case,  # type: ignore[arg-type]
        {
            "evaluation": {
                "score": Decimal("12.3456"),
                "nested": [Decimal("1.5"), {"qty": Decimal("0")}],
            }
        },
    )
    stored = case.case_metadata["procurement_manager"]["evaluation"]
    assert stored["score"] == 12.3456
    assert stored["nested"][0] == 1.5
    assert stored["nested"][1]["qty"] == 0.0


async def _async_value(value: object):
    return value
