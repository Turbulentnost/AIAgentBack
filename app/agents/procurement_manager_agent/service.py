from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any, Literal

from fastapi.encoders import jsonable_encoder
from langgraph.types import Command
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.procurement_manager_agent.allocation import allocate_materials_by_deadline
from app.agents.procurement_manager_agent.documents import (
    render_purchase_order_draft,
    render_rfq_draft,
)
from app.agents.procurement_manager_agent.graph import procurement_manager_graph
from app.agents.procurement_manager_agent.material_bank import get_material_bank
from app.agents.procurement_manager_agent.operations import MutationGate
from app.agents.procurement_manager_agent.pricing import (
    AMOUNT_FORMULA,
    estimate_nomenclature_amount,
    supplier_price_bounds,
)
from app.agents.procurement_manager_agent.schemas import (
    AgentResumeRequest,
    AgentRunRequest,
    AgentStatus,
    AllPositionsResponse,
    AllPositionsRow,
    ApprovalRecord,
    ApprovalRequest,
    ComparisonWeights,
    LineAmountEntry,
    LineAmountsUpdateRequest,
    MaterialBankResponse,
    NonconformityRequest,
    OperationStatus,
    PurchaseOrderDraft,
    PurchaseOrderDraftRequest,
    QuoteComparison,
    QuoteSubmission,
    RecommendationRecord,
    RecommendationRequest,
    RFQDraft,
    RFQDraftRequest,
    RFQLine,
    ShipmentEventRequest,
    Supplier,
    SupplierOffersResponse,
    SupplierQuote,
    SupplierSearchRequest,
    SupplierSearchResult,
    TopSupplierOffer,
    WorkspaceSummary,
)
from app.agents.procurement_manager_agent.supplier_ranking import (
    SCORE_FORMULA,
    collect_supplier_offers,
    price_bounds_from_offers,
    rank_supplier_offers,
)
from app.agents.procurement_manager_agent.scoring import compare_quotes
from app.agents.procurement_manager_agent.suppliers import HybridSupplierSearchService
from app.agents.procurement_role_agents.schemas import (
    ProcurementRoleAgentRequest,
    ProcurementRoleAgentResult,
)
from app.core.logging import get_logger
from app.models.enums import ConfidenceLevel, ProcurementCaseStatus, TaskStatus
from app.models.procurement import ProcurementCase, ProcurementCaseEvent
from app.models.task import Task
from app.services.procurement_orchestrator_service import ACTIVE_CASE_STATUSES

logger = get_logger(__name__)

AGENT_ID = "procurement_logistics_agent"
METADATA_KEY = "procurement_manager"

# Same statuses as the left-queue filter in /dashboard.
# Must NOT include engineer-stage statuses (human_required, agent_waiting, …) —
# those inflated KPI to hundreds of DB cases outside the manager list.
MANAGER_QUEUE_STATUSES = frozenset(
    {
        ProcurementCaseStatus.PURCHASE_DRAFT.value,
        ProcurementCaseStatus.APPROVAL_REQUIRED.value,
        ProcurementCaseStatus.ORDERED.value,
        ProcurementCaseStatus.PAYMENT_PENDING.value,
        ProcurementCaseStatus.IN_TRANSIT.value,
        ProcurementCaseStatus.RECEIVING.value,
        ProcurementCaseStatus.POSTING_REQUIRED.value,
        ProcurementCaseStatus.POSTED.value,
        ProcurementCaseStatus.NONCONFORMITY.value,
    }
)


def case_in_manager_queue(
    *,
    current_agent_id: str | None,
    status: str | None,
) -> bool:
    """Whether a case belongs to the procurement manager left-hand queue."""
    if current_agent_id == AGENT_ID:
        return True
    return (status or "") in MANAGER_QUEUE_STATUSES


class ProcurementManagerService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        supplier_search: HybridSupplierSearchService | None = None,
        use_graph: bool = True,
    ) -> None:
        self.db = db
        self.supplier_search = supplier_search or HybridSupplierSearchService()
        self.use_graph = use_graph

    async def run_role(self, payload: dict[str, Any]) -> ProcurementRoleAgentResult:
        request = ProcurementRoleAgentRequest.model_validate(payload)
        case = await self._case(uuid.UUID(request.case_id))
        if case is None:
            return ProcurementRoleAgentResult(
                agent_id=AGENT_ID,
                status="failed",
                summary="Закупочный кейс не найден.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=True,
                case_id=request.case_id,
                correlation_id=request.correlation_id,
                role_status="failed",
                output_data={},
            )

        metadata = self._workspace(case)
        metadata["lifecycle_state"] = "agent_running"
        metadata["handoff_received_at"] = datetime.now(UTC).isoformat()
        metadata.setdefault("payment_document_draft", None)
        metadata.setdefault("recommendation_audit", [])
        metadata.setdefault("purchase_order_drafts", [])
        self._save_workspace(case, metadata)
        await self._event(
            case,
            "procurement_manager_handoff_received",
            f"{request.idempotency_key}:manager-handoff",
            {"requested_operation": case.requested_operation},
        )
        try:
            agent_status = await self.agent_run(
                case.id,
                AgentRunRequest(
                    idempotency_key=f"auto-agent-run:{request.idempotency_key}"[:255],
                    allow_web_fallback=True,
                ),
            )
            lifecycle = agent_status.stage or "agent_running"
        except Exception as exc:  # noqa: BLE001 — handoff must not fail on agent start
            logger.warning(
                "procurement_manager_auto_agent_run_failed case_id=%s error=%s",
                case.id,
                str(exc)[:500],
            )
            agent_status = None
            lifecycle = "agent_running"
        metadata = self._workspace(case)
        metadata["lifecycle_state"] = (
            "approval_required"
            if agent_status and agent_status.paused_for_human
            else lifecycle
        )
        self._save_workspace(case, metadata)
        return ProcurementRoleAgentResult(
            agent_id=AGENT_ID,
            status="waiting_human",
            summary=(
                "Дефицит передан менеджеру по закупкам. "
                "Агент запущен: поиск, оценка и черновик заказа с HITL."
            ),
            data_confidence=ConfidenceLevel.MEDIUM,
            requires_human_review=True,
            case_id=request.case_id,
            correlation_id=request.correlation_id,
            role_status="waiting_human",
            wait_reason="Требуется подтверждение shortlist/заказа агентом менеджера.",
            output_data={
                "next_status": ProcurementCaseStatus.PURCHASE_DRAFT.value,
                "lifecycle_state": metadata["lifecycle_state"],
                "agent_stage": agent_status.stage if agent_status else None,
                "paused_for_human": bool(agent_status.paused_for_human)
                if agent_status
                else False,
                "payment_execution_allowed": False,
            },
        )

    async def search_suppliers(
        self,
        case_id: uuid.UUID,
        request: SupplierSearchRequest,
    ) -> SupplierSearchResult:
        case = await self.require_case(case_id)
        metadata = self._workspace(case)
        query = request.query or self._supplier_query_from_case(case)
        category = request.category or self._supplier_category_from_case(case)
        effective_request = request.model_copy(
            update={
                "query": query,
                "category": category,
                "idempotency_key": request.idempotency_key
                or f"supplier-search:{case.id}:{query.casefold()}"[:255],
            }
        )
        operation_id = str(effective_request.idempotency_key)
        previous = metadata.get("supplier_searches") or []
        replay = next(
            (
                item
                for item in previous
                if item.get("idempotency_key") == effective_request.idempotency_key
            ),
            None,
        )
        if replay:
            return SupplierSearchResult.model_validate(replay["result"])

        self._upsert_operation(
            case,
            operation_id=operation_id,
            operation="supplier_search",
            status="running",
        )
        timeout_seconds = float(
            os.environ.get("PROCUREMENT_MANAGER_SEARCH_TIMEOUT_SECONDS", "30")
        )
        try:
            # Manual supplier search stays outside the full HITL agent graph.
            result = await asyncio.wait_for(
                self.supplier_search.search(effective_request),
                timeout=timeout_seconds,
            )
            result = result.model_copy(
                update={
                    "operation_id": operation_id,
                    "pending": False,
                    "status": "completed",
                }
            )
        except TimeoutError:
            self._upsert_operation(
                case,
                operation_id=operation_id,
                operation="supplier_search",
                status="failed",
                error=(
                    f"Supplier search exceeded {timeout_seconds:.0f}s request timeout; "
                    "use GET operations/{operation_id} and retry with a new idempotency key."
                ),
            )
            metadata = self._workspace(case)
            metadata["lifecycle_state"] = "supplier_search_timeout"
            self._save_workspace(case, metadata)
            await self._event(
                case,
                "supplier_search_timeout",
                f"supplier-search-timeout:{operation_id}",
                {"operation_id": operation_id, "timeout_seconds": timeout_seconds},
            )
            return SupplierSearchResult(
                query=query,
                suppliers=[],
                sources_used=[],
                web_fallback_used=False,
                operation_id=operation_id,
                pending=False,
                status="failed",
            )
        except Exception as exc:
            self._upsert_operation(
                case,
                operation_id=operation_id,
                operation="supplier_search",
                status="failed",
                error=str(exc)[:1000],
            )
            raise

        previous.append(
            {
                "idempotency_key": effective_request.idempotency_key,
                "at": datetime.now(UTC).isoformat(),
                "result": result.model_dump(mode="json"),
            }
        )
        metadata = self._workspace(case)
        metadata["supplier_searches"] = previous
        metadata["suppliers"] = [item.model_dump(mode="json") for item in result.suppliers]
        metadata["lifecycle_state"] = "suppliers_identified"
        self._save_workspace(case, metadata)
        self._upsert_operation(
            case,
            operation_id=operation_id,
            operation="supplier_search",
            status="completed",
        )
        await self._event(
            case,
            "supplier_search_completed",
            str(effective_request.idempotency_key),
            {
                "count": len(result.suppliers),
                "sources": result.sources_used,
                "operation_id": operation_id,
            },
        )
        return result

    async def agent_run(
        self,
        case_id: uuid.UUID,
        request: AgentRunRequest | None = None,
    ) -> AgentStatus:
        """Idempotent start of the full search → evaluate → PO draft graph."""
        case = await self.require_case(case_id)
        payload = request or AgentRunRequest()
        idempotency_key = (
            payload.idempotency_key or f"agent-run:{case.id}:{datetime.now(UTC).date()}"
        )[:255]
        workspace = self._workspace(case)
        if workspace.get("agent_run_idempotency_key") == idempotency_key and workspace.get(
            "agent_stage"
        ):
            return await self.agent_status(case_id)

        query = payload.query or self._supplier_query_from_case(case)
        search_request = SupplierSearchRequest(
            query=query,
            category=self._supplier_category_from_case(case),
            allow_web_fallback=payload.allow_web_fallback,
            idempotency_key=f"agent-search:{idempotency_key}"[:255],
        )
        positions = [
            {
                "line_id": position.line_id,
                "nomenclature_id": position.nomenclature_id,
                "nomenclature_name": position.nomenclature_name,
                "quantity": str(position.quantity),
                "unit": position.unit or "шт",
                "required_date": (
                    position.required_date.isoformat() if position.required_date else None
                ),
            }
            for position in case.positions or []
            if not position.cancelled
        ]
        thread_id = f"procurement-manager:{case.id}:{idempotency_key}"
        config = {
            "configurable": {
                "thread_id": thread_id,
                "runtime": self.supplier_search,
            }
        }
        case_context = {
            **workspace,
            "positions": positions,
            "lines": positions,
            "required_date": case.required_date.isoformat() if case.required_date else None,
        }
        result = await procurement_manager_graph.ainvoke(
            {
                "case_id": str(case.id),
                "case_number": case.source_number or str(case.id),
                "case_context": case_context,
                "positions": positions,
                "request": search_request.model_dump(mode="json"),
            },
            config=config,
        )
        workspace = self._workspace(case)
        workspace["agent_run_idempotency_key"] = idempotency_key
        workspace["agent_thread_id"] = thread_id
        self._save_workspace(case, workspace)
        await self._persist_graph_state(case, result, "agent_run_paused")
        return await self.agent_status(case_id)

    async def agent_resume(
        self,
        case_id: uuid.UUID,
        request: AgentResumeRequest | dict[str, Any],
    ) -> AgentStatus:
        """HITL resume for shortlist / order draft approval (or reject)."""
        case = await self.require_case(case_id)
        if isinstance(request, AgentResumeRequest):
            decision = request.model_dump(mode="json")
        else:
            decision = dict(request)
            AgentResumeRequest.model_validate(decision)
        action = str(decision.get("action") or "")
        workspace = self._workspace(case)
        resume_key = decision.get("idempotency_key") or (
            f"agent-resume:{case.id}:{action}:{workspace.get('agent_stage')}"
        )
        resume_key = str(resume_key)[:255]
        prior = workspace.get("agent_resume_keys") or []
        if resume_key in prior:
            return await self.agent_status(case_id)

        thread_id = workspace.get("agent_thread_id") or f"procurement-manager:{case.id}"
        config = {
            "configurable": {
                "thread_id": thread_id,
                "runtime": self.supplier_search,
            }
        }
        result = await procurement_manager_graph.ainvoke(
            Command(resume=decision),
            config=config,
        )
        workspace = self._workspace(case)
        prior = list(workspace.get("agent_resume_keys") or [])
        prior.append(resume_key)
        workspace["agent_resume_keys"] = prior[-50:]
        self._save_workspace(case, workspace)
        await self._persist_graph_state(case, result, "agent_resumed")
        return await self.agent_status(case_id)

    async def resume_supplier_graph(
        self,
        case_id: uuid.UUID,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        """Legacy wrapper: resume via agent_resume and return graph snapshot."""
        status = await self.agent_resume(case_id, decision)
        case = await self.require_case(case_id)
        snapshot = dict(self._workspace(case).get("supplier_graph") or {})
        snapshot["agent_status"] = status.model_dump(mode="json")
        return snapshot

    async def agent_status(self, case_id: uuid.UUID) -> AgentStatus:
        case = await self.require_case(case_id)
        workspace = self._workspace(case)
        graph = dict(workspace.get("supplier_graph") or {})
        interrupt_payload = workspace.get("agent_interrupt") or {}
        po_drafts = workspace.get("purchase_order_drafts") or []
        latest_po = None
        if po_drafts:
            latest = po_drafts[-1]
            latest_po = latest.get("draft") if isinstance(latest, dict) else latest
        rfq_drafts = workspace.get("rfq_drafts") or []
        latest_rfq = None
        if rfq_drafts:
            latest = rfq_drafts[-1]
            latest_rfq = latest.get("draft") if isinstance(latest, dict) else latest
        if graph.get("rfq_draft"):
            latest_rfq = graph.get("rfq_draft")
        if graph.get("purchase_order_draft"):
            latest_po = graph.get("purchase_order_draft")
        return AgentStatus(
            case_id=str(case.id),
            stage=workspace.get("agent_stage") or graph.get("stage"),
            status=workspace.get("lifecycle_state") or graph.get("status"),
            paused_for_human=bool(workspace.get("paused_for_human") or graph.get("paused_for_human")),
            interrupt_type=(
                interrupt_payload.get("type")
                if isinstance(interrupt_payload, dict)
                else None
            ),
            recommendation=graph.get("recommendation") or workspace.get("recommendation"),
            evaluation=workspace.get("evaluation") or graph.get("evaluation"),
            rfq_draft=latest_rfq,
            purchase_order_draft=latest_po,
            comparison=workspace.get("comparison") or graph.get("comparison"),
            kpi_flags=dict(workspace.get("kpi_flags") or graph.get("kpi_flags") or {}),
            candidates_count=len(graph.get("candidates") or workspace.get("suppliers") or []),
            payment_execution_allowed=False,
        )

    async def create_purchase_order_draft(
        self,
        case_id: uuid.UUID,
        request: PurchaseOrderDraftRequest,
        *,
        approval_id: str | None = None,
    ) -> PurchaseOrderDraft:
        """Create a draft-only PO; never executes order/payment/1C write."""
        case = await self.require_case(case_id)
        metadata = self._workspace(case)
        drafts = metadata.get("purchase_order_drafts") or []
        replay = next(
            (
                item
                for item in drafts
                if item.get("idempotency_key") == request.idempotency_key
            ),
            None,
        )
        if replay:
            return PurchaseOrderDraft.model_validate(replay["draft"])

        approvals = [
            ApprovalRecord.model_validate(item["approval"])
            for item in metadata.get("approvals", [])
        ]
        approved = False
        if approval_id:
            MutationGate.authorize("create_supplier_order", approval_id, approvals)
            approved = True

        supplier_name = request.supplier_id
        for supplier in metadata.get("suppliers") or []:
            if supplier.get("supplier_id") == request.supplier_id:
                supplier_name = str(supplier.get("name") or supplier_name)
                break
        draft = render_purchase_order_draft(
            supplier_id=request.supplier_id,
            supplier_name=supplier_name,
            lines=request.lines,
            case_number=case.source_number or str(case.id),
            source_quote_id=request.source_quote_id,
        )
        draft_payload = draft.model_dump(mode="json")
        draft_payload["status"] = "approved_draft" if approved else "draft"
        draft_payload["payment_execution_allowed"] = False
        draft_payload["executed"] = False
        drafts.append(
            {
                "idempotency_key": request.idempotency_key,
                "draft": draft_payload,
                "approval_id": approval_id,
                "executed": False,
            }
        )
        metadata["purchase_order_drafts"] = drafts
        metadata["lifecycle_state"] = "purchase_order_draft"
        self._save_workspace(case, metadata)
        case.status = ProcurementCaseStatus.PURCHASE_DRAFT.value
        case.control_point = "purchase"
        await self._event(
            case,
            "purchase_order_draft_created",
            request.idempotency_key,
            {
                "po_id": draft.po_id,
                "executed": False,
                "payment_execution_allowed": False,
                "approval_id": approval_id,
            },
        )
        return PurchaseOrderDraft.model_validate(draft_payload)

    async def list_purchase_order_drafts(
        self,
        case_id: uuid.UUID,
    ) -> list[PurchaseOrderDraft]:
        case = await self.require_case(case_id)
        return [
            PurchaseOrderDraft.model_validate(
                item["draft"] if isinstance(item, dict) and "draft" in item else item
            )
            for item in self._workspace(case).get("purchase_order_drafts", [])
        ]

    async def get_purchase_order_draft(
        self,
        case_id: uuid.UUID,
        po_id: str,
    ) -> PurchaseOrderDraft:
        for draft in await self.list_purchase_order_drafts(case_id):
            if draft.po_id == po_id:
                return draft
        raise LookupError("Purchase order draft not found")

    async def _persist_graph_state(
        self,
        case: ProcurementCase,
        state: dict[str, Any],
        event_type: str,
    ) -> None:
        workspace = self._workspace(case)
        interrupt_raw = state.get("__interrupt__") or ()
        interrupt_payload: dict[str, Any] | None = None
        if interrupt_raw:
            first = interrupt_raw[0]
            value = getattr(first, "value", first)
            interrupt_payload = dict(value) if isinstance(value, dict) else {"value": value}

        snapshot = {
            key: value
            for key, value in state.items()
            if key not in {"runtime", "__interrupt__"}
        }
        paused = bool(interrupt_raw)
        snapshot["paused_for_human"] = paused

        workspace["supplier_graph"] = snapshot
        workspace["agent_stage"] = state.get("stage") or workspace.get("agent_stage")
        workspace["paused_for_human"] = paused
        workspace["agent_interrupt"] = interrupt_payload
        workspace["kpi_flags"] = dict(state.get("kpi_flags") or workspace.get("kpi_flags") or {})
        if state.get("evaluation") is not None:
            workspace["evaluation"] = state.get("evaluation")
        if state.get("comparison") is not None:
            workspace["comparison"] = state.get("comparison")
        if state.get("recommendation") is not None:
            workspace["recommendation"] = state.get("recommendation")
        if state.get("candidates"):
            workspace["suppliers"] = list(state.get("candidates") or [])
        if state.get("quotes"):
            workspace["quotes"] = [
                {"idempotency_key": f"graph-quote:{item.get('quote_id')}", "quote": item}
                for item in state.get("quotes") or []
                if isinstance(item, dict)
            ]

        rfq_draft = state.get("rfq_draft")
        if isinstance(rfq_draft, dict) and rfq_draft.get("rfq_id"):
            drafts = list(workspace.get("rfq_drafts") or [])
            existing = next(
                (
                    item
                    for item in drafts
                    if (item.get("draft") or {}).get("rfq_id") == rfq_draft.get("rfq_id")
                ),
                None,
            )
            entry = {
                "idempotency_key": f"agent-rfq:{rfq_draft.get('rfq_id')}",
                "draft": rfq_draft,
            }
            if existing is None:
                drafts.append(entry)
            else:
                existing.update(entry)
            workspace["rfq_drafts"] = drafts

        po_draft = state.get("purchase_order_draft")
        if isinstance(po_draft, dict) and po_draft.get("po_id"):
            po_payload = dict(po_draft)
            po_payload["payment_execution_allowed"] = False
            po_payload["executed"] = False
            if state.get("status") == "order_draft_approved":
                po_payload["status"] = "approved_draft"
            else:
                po_payload.setdefault("status", "draft")
            drafts = list(workspace.get("purchase_order_drafts") or [])
            existing = next(
                (
                    item
                    for item in drafts
                    if (item.get("draft") or {}).get("po_id") == po_payload.get("po_id")
                ),
                None,
            )
            entry = {
                "idempotency_key": f"agent-po:{po_payload.get('po_id')}",
                "draft": po_payload,
                "executed": False,
            }
            if existing is None:
                drafts.append(entry)
            else:
                existing.update(entry)
            workspace["purchase_order_drafts"] = drafts

        if paused:
            workspace["lifecycle_state"] = "approval_required"
        else:
            workspace["lifecycle_state"] = state.get("status") or workspace.get(
                "lifecycle_state"
            ) or "agent_running"
        self._save_workspace(case, workspace)
        if case.status not in {
            ProcurementCaseStatus.ORDERED.value,
            ProcurementCaseStatus.IN_TRANSIT.value,
            ProcurementCaseStatus.RECEIVING.value,
        }:
            case.status = ProcurementCaseStatus.PURCHASE_DRAFT.value
            case.control_point = "purchase"
        await self._event(
            case,
            event_type,
            f"{event_type}:{case.id}:{workspace.get('agent_stage')}:{paused}",
            {
                "status": snapshot.get("status"),
                "stage": snapshot.get("stage"),
                "candidate_count": len(snapshot.get("candidates") or []),
                "web_fallback_used": snapshot.get("web_fallback_used", False),
                "paused_for_human": paused,
                "interrupt_type": (
                    interrupt_payload.get("type") if interrupt_payload else None
                ),
                "payment_execution_allowed": False,
            },
        )

    async def list_suppliers(self, case_id: uuid.UUID) -> list[Supplier]:
        case = await self.require_case(case_id)
        return [
            Supplier.model_validate(item)
            for item in self._workspace(case).get("suppliers", [])
        ]

    async def create_rfq_draft(
        self,
        case_id: uuid.UUID,
        request: RFQDraftRequest,
    ) -> RFQDraft:
        case = await self.require_case(case_id)
        metadata = self._workspace(case)
        drafts = metadata.get("rfq_drafts") or []
        replay = next(
            (item for item in drafts if item.get("idempotency_key") == request.idempotency_key),
            None,
        )
        if replay:
            return RFQDraft.model_validate(replay["draft"])
        suppliers = [
            Supplier.model_validate(item)
            for item in metadata.get("suppliers", [])
            if item.get("supplier_id") in request.supplier_ids
        ]
        draft = render_rfq_draft(
            request,
            suppliers,
            case_number=case.source_number or str(case.id),
        )
        drafts.append(
            {"idempotency_key": request.idempotency_key, "draft": draft.model_dump(mode="json")}
        )
        metadata["rfq_drafts"] = drafts
        metadata["lifecycle_state"] = "rfq_draft"
        self._save_workspace(case, metadata)
        case.status = ProcurementCaseStatus.PURCHASE_DRAFT.value
        case.control_point = "purchase"
        await self._event(
            case,
            "rfq_draft_created",
            request.idempotency_key,
            {"rfq_id": draft.rfq_id},
        )
        return draft

    async def list_rfq_drafts(self, case_id: uuid.UUID) -> list[RFQDraft]:
        case = await self.require_case(case_id)
        return [
            RFQDraft.model_validate(item["draft"])
            for item in self._workspace(case).get("rfq_drafts", [])
        ]

    async def submit_quote(
        self,
        case_id: uuid.UUID,
        submission: QuoteSubmission,
    ) -> SupplierQuote:
        case = await self.require_case(case_id)
        metadata = self._workspace(case)
        quotes = metadata.get("quotes") or []
        existing = next(
            (
                item
                for item in quotes
                if item.get("idempotency_key") == submission.idempotency_key
            ),
            None,
        )
        if existing:
            return SupplierQuote.model_validate(existing["quote"])
        quotes.append(
            {
                "idempotency_key": submission.idempotency_key,
                "quote": submission.quote.model_dump(mode="json"),
            }
        )
        metadata["quotes"] = quotes
        metadata["lifecycle_state"] = "quotes_received"
        self._save_workspace(case, metadata)
        await self._event(
            case,
            "supplier_quote_recorded",
            submission.idempotency_key,
            {"quote_id": submission.quote.quote_id, "supplier_id": submission.quote.supplier_id},
        )
        return submission.quote

    async def list_quotes(self, case_id: uuid.UUID) -> list[SupplierQuote]:
        case = await self.require_case(case_id)
        return [
            SupplierQuote.model_validate(item["quote"])
            for item in self._workspace(case).get("quotes", [])
        ]

    async def comparison(
        self,
        case_id: uuid.UUID,
        weights: ComparisonWeights | None = None,
    ) -> QuoteComparison:
        case = await self.require_case(case_id)
        comparison = compare_quotes(await self.list_quotes(case_id), weights)
        metadata = self._workspace(case)
        metadata["comparison"] = comparison.model_dump(mode="json")
        metadata["lifecycle_state"] = "comparison_ready"
        self._save_workspace(case, metadata)
        await self.db.flush()
        return comparison

    async def recommendation(
        self,
        case_id: uuid.UUID,
        request: RecommendationRequest,
    ) -> RecommendationRecord:
        case = await self.require_case(case_id)
        metadata = self._workspace(case)
        audit = metadata.get("recommendation_audit") or []
        replay = next(
            (
                item
                for item in audit
                if item.get("idempotency_key") == request.idempotency_key
            ),
            None,
        )
        if replay:
            return RecommendationRecord.model_validate(replay["recommendation"])

        quotes = await self.list_quotes(case_id)
        quote = next((item for item in quotes if item.quote_id == request.quote_id), None)
        if quote is None:
            raise ValueError("Quote not found")
        if quote.supplier_id != request.supplier_id:
            raise ValueError("Quote does not belong to selected supplier")
        comparison = await self.comparison(case_id)
        score = next(
            (item.final_score for item in comparison.scores if item.quote_id == quote.quote_id),
            None,
        )
        approvals = [
            ApprovalRecord.model_validate(item["approval"])
            for item in metadata.get("approvals", [])
        ]
        supplier_approved = False
        price_approved = False
        if request.supplier_selection_approval_id:
            MutationGate.authorize(
                "select_supplier",
                request.supplier_selection_approval_id,
                approvals,
            )
            supplier_approved = True
        if request.price_approval_id:
            MutationGate.authorize("approve_price", request.price_approval_id, approvals)
            price_approved = True

        recommendation = RecommendationRecord(
            recommendation_id=str(uuid.uuid4()),
            supplier_id=request.supplier_id,
            quote_id=request.quote_id,
            total=quote.total,
            currency=quote.currency,
            score=score,
            rationale=request.rationale,
            status="approved" if supplier_approved and price_approved else "approval_required",
            supplier_selection_approval_id=request.supplier_selection_approval_id,
            price_approval_id=request.price_approval_id,
            created_at=datetime.now(UTC),
        )
        serialized = recommendation.model_dump(mode="json")
        metadata["recommendation"] = serialized
        audit.append(
            {
                "idempotency_key": request.idempotency_key,
                "at": datetime.now(UTC).isoformat(),
                "recommendation": serialized,
            }
        )
        metadata["recommendation_audit"] = audit
        metadata["lifecycle_state"] = recommendation.status
        self._save_workspace(case, metadata)
        case.status = ProcurementCaseStatus.APPROVAL_REQUIRED.value
        await self._event(
            case,
            "procurement_recommendation_recorded",
            request.idempotency_key,
            serialized,
        )
        return recommendation

    async def record_approval(
        self,
        case_id: uuid.UUID,
        request: ApprovalRequest,
        *,
        actor_user_id: str,
    ) -> ApprovalRecord:
        case = await self.require_case(case_id)
        metadata = self._workspace(case)
        approvals = metadata.get("approvals") or []
        replay = next(
            (
                item
                for item in approvals
                if item.get("idempotency_key") == request.idempotency_key
            ),
            None,
        )
        if replay:
            return ApprovalRecord.model_validate(replay["approval"])
        approval_id = request.approval_id or str(uuid.uuid4())
        approval = ApprovalRecord(
            approval_id=approval_id,
            operation=request.operation,
            status=request.status,
            comment=request.comment,
            actor_user_id=actor_user_id,
            created_at=datetime.now(UTC),
        )
        existing_approval = next(
            (
                item
                for item in reversed(approvals)
                if (item.get("approval") or {}).get("approval_id") == approval_id
                and (item.get("approval") or {}).get("operation") == request.operation
            ),
            None,
        )
        approval_payload = {
            "idempotency_key": request.idempotency_key,
            "approval": approval.model_dump(mode="json"),
        }
        if existing_approval is None:
            approvals.append(approval_payload)
        else:
            existing_approval.update(approval_payload)
        metadata["approvals"] = approvals
        operations = metadata.get("operations") or []
        operation = next(
            (item for item in operations if item.get("operation_id") == approval_id),
            None,
        )
        operation_status: Literal["approval_required", "approved", "rejected"]
        if request.status == "requested":
            operation_status = "approval_required"
        elif request.status == "approved":
            operation_status = "approved"
        else:
            operation_status = "rejected"
        operation_payload = OperationStatus(
            operation_id=approval_id,
            case_id=str(case.id),
            operation=request.operation,
            status=operation_status,
            approval_id=approval_id,
            updated_at=datetime.now(UTC),
        ).model_dump(mode="json")
        if operation is None:
            operations.append(operation_payload)
        else:
            operation.update(operation_payload)
        metadata["operations"] = operations
        metadata["lifecycle_state"] = (
            "approved" if approval.status == "approved" else "approval_required"
        )
        self._save_workspace(case, metadata)
        case.status = (
            ProcurementCaseStatus.ORDERED.value
            if approval.status == "approved" and approval.operation == "create_supplier_order"
            else ProcurementCaseStatus.APPROVAL_REQUIRED.value
        )
        await self._event(
            case,
            "procurement_approval_recorded",
            request.idempotency_key,
            approval.model_dump(mode="json"),
        )
        return approval

    async def list_approvals(self, case_id: uuid.UUID) -> list[ApprovalRecord]:
        case = await self.require_case(case_id)
        return [
            ApprovalRecord.model_validate(item["approval"])
            for item in self._workspace(case).get("approvals", [])
        ]

    async def record_shipment(
        self,
        case_id: uuid.UUID,
        request: ShipmentEventRequest,
    ) -> dict[str, Any]:
        case = await self.require_case(case_id)
        metadata = self._workspace(case)
        approvals = [
            ApprovalRecord.model_validate(item["approval"])
            for item in metadata.get("approvals", [])
        ]
        MutationGate.authorize("record_shipment", request.approval_id, approvals)
        events = metadata.get("shipment_events") or []
        replay = next(
            (item for item in events if item.get("idempotency_key") == request.idempotency_key),
            None,
        )
        if replay:
            return replay["event"]
        event = request.event.model_dump(mode="json")
        events.append({"idempotency_key": request.idempotency_key, "event": event})
        metadata["shipment_events"] = events
        metadata["lifecycle_state"] = request.event.event_type
        self._save_workspace(case, metadata)
        status_map = {
            "ordered": ProcurementCaseStatus.ORDERED.value,
            "dispatched": ProcurementCaseStatus.IN_TRANSIT.value,
            "in_transit": ProcurementCaseStatus.IN_TRANSIT.value,
            "delayed": ProcurementCaseStatus.IN_TRANSIT.value,
            "received": ProcurementCaseStatus.RECEIVING.value,
        }
        case.status = status_map[request.event.event_type]
        case.control_point = "receipt" if request.event.event_type == "received" else "delivery"
        await self._event(case, "shipment_event_recorded", request.idempotency_key, event)
        return event

    async def list_shipment_events(self, case_id: uuid.UUID) -> list[dict[str, Any]]:
        case = await self.require_case(case_id)
        return [
            dict(item["event"])
            for item in self._workspace(case).get("shipment_events", [])
        ]

    async def handoff_nonconformity(
        self,
        case_id: uuid.UUID,
        request: NonconformityRequest,
    ) -> dict[str, Any]:
        case = await self.require_case(case_id)
        metadata = self._workspace(case)
        rows = metadata.get("nonconformities") or []
        replay = next(
            (item for item in rows if item.get("idempotency_key") == request.idempotency_key),
            None,
        )
        if replay:
            return replay["nonconformity"]
        item = request.nonconformity.model_dump(mode="json")
        rows.append({"idempotency_key": request.idempotency_key, "nonconformity": item})
        metadata["nonconformities"] = rows
        metadata["lifecycle_state"] = "nonconformity"
        self._save_workspace(case, metadata)
        root_metadata = dict(case.case_metadata or {})
        root_metadata["next_quality_agent"] = "otk_head_agent"
        root_metadata["quality_stage"] = ProcurementCaseStatus.NONCONFORMITY.value
        root_metadata["quality_context"] = {"nonconformity": item}
        case.case_metadata = root_metadata
        if case.current_task_id:
            task = await self.db.get(Task, case.current_task_id)
            if task is not None:
                task.status = TaskStatus.COMPLETED
                task.finished_at = datetime.now(UTC)
        case.current_task_id = None
        case.status = ProcurementCaseStatus.NONCONFORMITY.value
        case.current_agent_id = "otk_head_agent"
        case.assigned_agents = ["otk_head_agent"]
        case.control_point = "receipt"
        await self._event(
            case,
            "nonconformity_handed_to_quality",
            request.idempotency_key,
            {**item, "next_agent": "otk_head_agent"},
        )
        return {**item, "next_agent": "otk_head_agent"}

    def _upsert_operation(
        self,
        case: ProcurementCase,
        *,
        operation_id: str,
        operation: str,
        status: Literal[
            "draft",
            "running",
            "completed",
            "approval_required",
            "approved",
            "executed",
            "rejected",
            "failed",
        ],
        approval_id: str | None = None,
        error: str | None = None,
    ) -> OperationStatus:
        metadata = self._workspace(case)
        operations = metadata.get("operations") or []
        payload = OperationStatus(
            operation_id=operation_id,
            case_id=str(case.id),
            operation=operation,
            status=status,
            approval_id=approval_id,
            error=error,
            updated_at=datetime.now(UTC),
        ).model_dump(mode="json")
        existing = next(
            (item for item in operations if item.get("operation_id") == operation_id),
            None,
        )
        if existing is None:
            operations.append(payload)
        else:
            existing.update(payload)
        metadata["operations"] = operations
        self._save_workspace(case, metadata)
        return OperationStatus.model_validate(payload)

    async def operation_status(
        self,
        case_id: uuid.UUID,
        operation_id: str,
    ) -> OperationStatus | None:
        case = await self.require_case(case_id)
        for item in self._workspace(case).get("operations", []):
            if item.get("operation_id") == operation_id:
                return OperationStatus.model_validate(item)
        return None

    async def global_operation_status(
        self,
        operation_id: str,
    ) -> OperationStatus | None:
        cases = (await self.db.execute(select(ProcurementCase))).scalars().all()
        for case in cases:
            for item in self._workspace(case).get("operations", []):
                if item.get("operation_id") == operation_id:
                    payload = {**item, "case_id": str(case.id)}
                    return OperationStatus.model_validate(payload)
        return None

    async def workspace_payload(self, case_id: uuid.UUID) -> dict[str, Any]:
        case = await self.require_case(case_id)
        payload = self._public_workspace(self._workspace(case))
        meta = dict(case.case_metadata or {})
        # Surface project/demo titles for the manager workspace header.
        if meta.get("need_title"):
            payload["need_title"] = meta.get("need_title")
        if meta.get("project_code"):
            payload["project_code"] = meta.get("project_code")
        if meta.get("project_name"):
            payload["project_name"] = meta.get("project_name")
        allocation = await self.allocate_coverage()
        case_coverage = (allocation.get("case_index") or {}).get(str(case_id))
        payload["coverage"] = case_coverage
        payload["order_coverage"] = (
            {
                "tone": case_coverage.get("tone"),
                "label": case_coverage.get("label"),
                "covered_count": case_coverage.get("covered_count") or 0,
                "positions_count": case_coverage.get("positions_count") or 0,
                "uncovered_positions_count": case_coverage.get("uncovered_positions_count") or 0,
                "has_suppliers": any(
                    Decimal(str(line.get("from_supplier") or 0)) > 0
                    or line.get("coverage_source") in {"supplier", "mixed"}
                    for line in case_coverage.get("lines") or []
                ),
                "lines": case_coverage.get("lines") or [],
            }
            if case_coverage
            else {
                "tone": "uncovered",
                "label": "Полностью необеспечен",
                "covered_count": 0,
                "positions_count": 0,
                "uncovered_positions_count": 0,
                "has_suppliers": False,
                "lines": [],
            }
        )
        payload["material_allocation"] = {"summary": allocation.get("summary")}
        return payload

    async def material_bank(self) -> MaterialBankResponse:
        bank = get_material_bank()
        public = bank.to_public()
        return MaterialBankResponse.model_validate(public)

    async def allocate_coverage(self) -> dict[str, Any]:
        cases = await self._manager_cases()
        return allocate_materials_by_deadline(cases, bank=get_material_bank())

    async def all_positions(self) -> AllPositionsResponse:
        """Aggregate queue nomenclature with supplier price_min/max and estimate."""
        cases = await self._manager_cases()
        bank = get_material_bank()
        allocation = allocate_materials_by_deadline(cases, bank=bank)
        bounds = supplier_price_bounds(bank)
        coverage_by_nom = {
            str(row.get("nomenclature_id") or "").strip().casefold(): row
            for row in allocation.get("by_nomenclature") or []
            if row.get("nomenclature_id")
        }

        buckets: dict[str, dict[str, Any]] = {}
        for case in cases:
            workspace = self._workspace(case)
            line_amounts = dict(workspace.get("line_amounts") or {})
            for position in case.positions or []:
                if position.cancelled:
                    continue
                qty = Decimal(str(position.quantity or 0))
                if qty <= 0:
                    continue
                nom_id = str(position.nomenclature_id or "").strip()
                nom_name = position.nomenclature_name
                key = nom_id.casefold() if nom_id else (nom_name or "").strip().casefold()
                if not key:
                    key = f"line:{position.line_id}"
                manual = line_amounts.get(position.line_id) or {}
                override = manual.get("unit_price")
                if override is None and manual.get("amount") is not None and qty > 0:
                    override = Decimal(str(manual["amount"])) / qty
                elif override is not None:
                    override = Decimal(str(override))
                currency = str(manual.get("currency") or "RUB").upper()
                bucket = buckets.get(key)
                if bucket is None:
                    buckets[key] = {
                        "nomenclature_id": nom_id or None,
                        "nomenclature_name": nom_name or nom_id or "Без названия",
                        "unit": position.unit or "шт",
                        "quantity": qty,
                        "line_overrides": [(qty, override)],
                        "currency": currency,
                        "has_manual_override": override is not None,
                        "positions_count": 1,
                    }
                    continue
                bucket["quantity"] += qty
                bucket["line_overrides"].append((qty, override))
                bucket["positions_count"] += 1
                if override is not None:
                    bucket["has_manual_override"] = True
                if not bucket.get("nomenclature_name") and nom_name:
                    bucket["nomenclature_name"] = nom_name

        rows: list[AllPositionsRow] = []
        total = Decimal("0")
        any_total = False
        for key, bucket in buckets.items():
            bound = bounds.get(key)
            if bound is None and bucket.get("nomenclature_id"):
                bound = bounds.get(str(bucket["nomenclature_id"]).casefold())
            price_min = bound["price_min"] if bound else None
            price_max = bound["price_max"] if bound else None
            cov = coverage_by_nom.get(key) or {}
            from_supplier = None
            raw_from_supplier = cov.get("from_supplier")
            if raw_from_supplier is not None and raw_from_supplier != "":
                from_supplier = Decimal(str(raw_from_supplier))
            nom_id = bucket.get("nomenclature_id")
            offers = (
                collect_supplier_offers(str(nom_id), bank=bank) if nom_id else []
            )
            estimate = estimate_nomenclature_amount(
                bucket["quantity"],
                price_min=price_min,
                line_overrides=bucket["line_overrides"],
                coverage_source=cov.get("coverage_source"),
                from_supplier=from_supplier,
                offers=offers,
            )
            if estimate.amount is not None:
                total += estimate.amount
                any_total = True
            top_suppliers: list[TopSupplierOffer] = []
            if nom_id:
                ranked = rank_supplier_offers(
                    str(nom_id),
                    bucket["quantity"],
                    bank=bank,
                    top_n=3,
                )
                top_suppliers = [TopSupplierOffer.model_validate(item) for item in ranked]
            rows.append(
                AllPositionsRow(
                    nomenclature_id=bucket.get("nomenclature_id"),
                    nomenclature_name=bucket.get("nomenclature_name"),
                    unit=bucket.get("unit") or "шт",
                    quantity=bucket["quantity"],
                    price_min=price_min,
                    price_max=price_max,
                    avg_unit_price=estimate.avg_unit_price,
                    estimated_amount=estimate.amount,
                    amount=estimate.amount,
                    overpay=estimate.overpay,
                    amount_source=estimate.source,
                    amount_formula=AMOUNT_FORMULA,
                    currency=bucket.get("currency") or "RUB",
                    coverage_source=cov.get("coverage_source"),
                    coverage_source_label=cov.get("coverage_source_label"),
                    positions_count=int(bucket.get("positions_count") or 0),
                    has_manual_override=bool(bucket.get("has_manual_override")),
                    top_suppliers=top_suppliers,
                )
            )
        rows.sort(
            key=lambda item: str(item.nomenclature_name or item.nomenclature_id or ""),
        )
        return AllPositionsResponse(
            rows=rows,
            total_estimated_amount=total if any_total else None,
            currency="RUB",
            amount_formula=AMOUNT_FORMULA,
            price_formula=AMOUNT_FORMULA,
            score_formula=SCORE_FORMULA,
        )

    async def supplier_offers_for_case(
        self,
        case_id: uuid.UUID,
        *,
        nomenclature_id: str,
        need_qty: Decimal | None = None,
        top_n: int = 3,
    ) -> SupplierOffersResponse:
        """Top-N ranked supplier offers for a case nomenclature need."""
        case = await self.require_case(case_id)
        nom = nomenclature_id.strip()
        if not nom:
            raise ValueError("nomenclature is required")

        resolved_need = need_qty
        unit = "шт"
        nomenclature_name: str | None = None
        if resolved_need is None:
            total = Decimal("0")
            for position in case.positions or []:
                if position.cancelled:
                    continue
                pid = str(position.nomenclature_id or "").strip()
                if pid.casefold() != nom.casefold():
                    continue
                total += Decimal(str(position.quantity or 0))
                unit = position.unit or unit
                nomenclature_name = position.nomenclature_name or nomenclature_name
            resolved_need = total

        bank = get_material_bank()
        raw_offers = collect_supplier_offers(nom, bank=bank)
        if nomenclature_name is None and raw_offers:
            nomenclature_name = raw_offers[0].get("nomenclature_name")
        if raw_offers and raw_offers[0].get("unit"):
            unit = str(raw_offers[0]["unit"])
        price_min, price_max = price_bounds_from_offers(raw_offers)
        # Align with global table bounds when offers exist in bank pricing.
        bounds = supplier_price_bounds(bank).get(nom.casefold())
        if bounds:
            price_min = bounds["price_min"]
            price_max = bounds["price_max"]

        ranked = rank_supplier_offers(
            nom,
            resolved_need or Decimal("0"),
            bank=bank,
            top_n=top_n,
        )
        return SupplierOffersResponse(
            nomenclature_id=nom,
            nomenclature_name=nomenclature_name,
            need_qty=resolved_need or Decimal("0"),
            unit=unit,
            price_min=price_min,
            price_max=price_max,
            score_formula=SCORE_FORMULA,
            top_suppliers=[TopSupplierOffer.model_validate(item) for item in ranked],
        )

    async def workspace_summary(self) -> WorkspaceSummary:
        allocation = await self.allocate_coverage()
        summary = allocation.get("summary") or {}
        queue_cases = int(summary.get("total_orders_count") or 0)
        positions_count = int(summary.get("positions_count") or 0)
        uncovered_orders = int(summary.get("uncovered_orders_count") or 0)
        uncovered_positions = int(summary.get("uncovered_positions_count") or 0)
        active_suppliers = int(summary.get("active_suppliers_count") or 0)
        # Invariants: KPI must be scoped to the manager queue only.
        if uncovered_orders > queue_cases or uncovered_positions > positions_count:
            logger.warning(
                "procurement_manager KPI invariant violated: "
                "queue_cases=%s uncovered_orders=%s positions=%s uncovered_positions=%s",
                queue_cases,
                uncovered_orders,
                positions_count,
                uncovered_positions,
            )
        else:
            logger.info(
                "procurement_manager workspace_summary: "
                "queue_cases=%s uncovered_orders=%s positions=%s "
                "uncovered_positions=%s suppliers=%s",
                queue_cases,
                uncovered_orders,
                positions_count,
                uncovered_positions,
                active_suppliers,
            )
        return WorkspaceSummary(
            uncovered_orders_count=uncovered_orders,
            active_suppliers_count=active_suppliers,
            uncovered_positions_count=uncovered_positions,
            # Compat: former nomenclature_count now = uncovered position lines
            nomenclature_count=uncovered_positions,
            total_orders_count=queue_cases,
            ready_orders_count=int(summary.get("ready_orders_count") or 0),
            attention_orders_count=int(summary.get("attention_orders_count") or 0),
            positions_count=positions_count,
            need_quantity_total=Decimal(str(summary.get("need_quantity_total") or 0)),
            bank_quantity_total=Decimal(str(summary.get("bank_quantity_total") or 0)),
            warehouses_count=int(summary.get("warehouses_count") or 0),
            generated_at=datetime.now(UTC),
        )

    async def enrich_dashboard_cases(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Attach deadline-based coverage status to dashboard case cards."""
        allocation = await self.allocate_coverage()
        case_index = allocation.get("case_index") or {}
        for group in payload.get("groups", []):
            for item in group.get("cases") or []:
                case_id = str(item.get("id") or "").strip().casefold()
                coverage = case_index.get(case_id)
                if coverage is None:
                    # Also try raw id (case_index keys are str(uuid) lowercased below).
                    coverage = case_index.get(str(item.get("id") or ""))
                positions_count = int(
                    (coverage or {}).get("positions_count")
                    or item.get("positions_count")
                    or 0
                )
                if coverage is None:
                    # Unknown allocation → attention if there are positions, not false uncovered.
                    order_coverage = {
                        "tone": "attention" if positions_count > 0 else "uncovered",
                        "label": (
                            "Требуют внимания" if positions_count > 0 else "Полностью необеспечен"
                        ),
                        "covered_count": 0,
                        "positions_count": positions_count,
                        "uncovered_positions_count": positions_count,
                        "has_suppliers": False,
                    }
                else:
                    order_coverage = {
                        "tone": coverage.get("tone"),
                        "label": coverage.get("label"),
                        "covered_count": coverage.get("covered_count") or 0,
                        "positions_count": coverage.get("positions_count") or 0,
                        "uncovered_positions_count": coverage.get("uncovered_positions_count")
                        or 0,
                        "has_suppliers": any(
                            (
                                line.get("from_supplier")
                                and Decimal(str(line["from_supplier"])) > 0
                            )
                            or line.get("coverage_source") in {"supplier", "mixed"}
                            for line in coverage.get("lines") or []
                        ),
                        "needed_quantity": coverage.get("needed_quantity"),
                        "covered_quantity": coverage.get("covered_quantity"),
                        "deficit_quantity": coverage.get("deficit_quantity"),
                        "lines": coverage.get("lines") or [],
                    }
                item["order_coverage"] = order_coverage
                item["coverage"] = order_coverage
                # Nested copy so clients reading procurement_manager.* still see bank tone.
                pm = dict(item.get("procurement_manager") or {})
                pm["order_coverage"] = order_coverage
                item["procurement_manager"] = pm
        payload["material_allocation_summary"] = allocation.get("summary")
        return payload

    async def save_line_amounts(
        self,
        case_id: uuid.UUID,
        payload: LineAmountsUpdateRequest,
    ) -> dict[str, Any]:
        case = await self.require_case(case_id)
        workspace = self._workspace(case)
        current = dict(workspace.get("line_amounts") or {})
        valid_line_ids = {
            position.line_id
            for position in case.positions or []
            if not position.cancelled
        }
        for entry in payload.lines:
            if entry.line_id not in valid_line_ids:
                continue
            amount = entry.amount
            unit_price = entry.unit_price
            quantity = next(
                (
                    Decimal(str(position.quantity))
                    for position in case.positions or []
                    if position.line_id == entry.line_id
                ),
                Decimal("0"),
            )
            if amount is None and unit_price is not None and quantity > 0:
                amount = (unit_price * quantity).quantize(Decimal("0.01"))
            if unit_price is None and amount is not None and quantity > 0:
                unit_price = (amount / quantity).quantize(Decimal("0.0001"))
            if amount is None and unit_price is None:
                current.pop(entry.line_id, None)
                continue
            current[entry.line_id] = LineAmountEntry(
                line_id=entry.line_id,
                unit_price=unit_price,
                amount=amount,
                currency=(entry.currency or "RUB").upper(),
            ).model_dump(mode="json")
        workspace["line_amounts"] = current
        self._save_workspace(case, workspace)
        if payload.idempotency_key:
            await self._event(
                case,
                "procurement_manager_line_amounts_updated",
                payload.idempotency_key,
                {"lines": list(current.keys())},
            )
        await self.db.flush()
        return {"line_amounts": current}

    def build_estimate_xlsx(self, case: ProcurementCase) -> bytes:
        from openpyxl import Workbook
        from openpyxl.styles import Font

        workspace = self._workspace(case)
        line_amounts = dict(workspace.get("line_amounts") or {})
        quote_prices = self._quote_unit_prices(workspace)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Смета"
        headers = [
            "№",
            "Номенклатура",
            "Код",
            "Количество",
            "Ед.",
            "Цена",
            "Сумма",
            "Валюта",
            "Источник цены",
        ]
        for col, title in enumerate(headers, start=1):
            cell = sheet.cell(row=1, column=col, value=title)
            cell.font = Font(bold=True)

        total = Decimal("0")
        currency = "RUB"
        row_idx = 2
        for index, position in enumerate(case.positions or [], start=1):
            if position.cancelled:
                continue
            quantity = Decimal(str(position.quantity or 0))
            manual = line_amounts.get(position.line_id) or {}
            unit_price = manual.get("unit_price")
            amount = manual.get("amount")
            source = "вручную" if unit_price is not None or amount is not None else ""
            if unit_price is None and amount is None:
                quoted = quote_prices.get(position.line_id)
                if quoted is not None:
                    unit_price = quoted
                    source = "КП"
            if amount is None and unit_price is not None:
                amount = (Decimal(str(unit_price)) * quantity).quantize(Decimal("0.01"))
            if amount is not None:
                total += Decimal(str(amount))
            line_currency = str(manual.get("currency") or currency)
            currency = line_currency
            sheet.cell(row=row_idx, column=1, value=index)
            sheet.cell(
                row=row_idx,
                column=2,
                value=position.nomenclature_name or position.nomenclature_id,
            )
            sheet.cell(row=row_idx, column=3, value=position.nomenclature_id)
            sheet.cell(row=row_idx, column=4, value=float(quantity))
            sheet.cell(row=row_idx, column=5, value=position.unit or "шт")
            sheet.cell(
                row=row_idx,
                column=6,
                value=float(unit_price) if unit_price is not None else None,
            )
            sheet.cell(
                row=row_idx,
                column=7,
                value=float(amount) if amount is not None else None,
            )
            sheet.cell(row=row_idx, column=8, value=line_currency)
            sheet.cell(row=row_idx, column=9, value=source or "—")
            row_idx += 1

        total_label = sheet.cell(row=row_idx + 1, column=6, value="Итого")
        total_label.font = Font(bold=True)
        total_value = sheet.cell(row=row_idx + 1, column=7, value=float(total))
        total_value.font = Font(bold=True)
        sheet.cell(row=row_idx + 1, column=8, value=currency)
        sheet.cell(row=row_idx + 3, column=1, value="Заказ")
        sheet.cell(
            row=row_idx + 3,
            column=2,
            value=case.source_number or case.source_1c_ref,
        )
        sheet.cell(row=row_idx + 4, column=1, value="Подразделение")
        sheet.cell(row=row_idx + 4, column=2, value=case.department_name or "")
        buffer = BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    async def require_case(self, case_id: uuid.UUID) -> ProcurementCase:
        case = await self._case(case_id)
        if case is None:
            raise LookupError("Procurement case not found")
        return case

    async def _case(self, case_id: uuid.UUID) -> ProcurementCase | None:
        return await self.db.scalar(
            select(ProcurementCase)
            .options(selectinload(ProcurementCase.positions))
            .where(ProcurementCase.id == case_id)
        )

    @staticmethod
    def _workspace(case: ProcurementCase) -> dict[str, Any]:
        return dict((case.case_metadata or {}).get(METADATA_KEY) or {})

    @staticmethod
    def _save_workspace(case: ProcurementCase, workspace: dict[str, Any]) -> None:
        metadata = dict(case.case_metadata or {})
        # Graph evaluation / ranking embeds Decimal; JSONB requires JSON-safe values.
        metadata[METADATA_KEY] = jsonable_encoder(workspace)
        case.case_metadata = metadata

    async def _manager_cases(self) -> list[ProcurementCase]:
        """Cases in the manager left-hand queue (same set as /dashboard).

        Previous bug: status filter included engineer-stage statuses
        (human_required, agent_waiting), so KPI counted hundreds of cases
        outside the visible queue (e.g. 503 uncovered orders / 3487 lines).
        """
        result = await self.db.scalars(
            select(ProcurementCase)
            .options(selectinload(ProcurementCase.positions))
            .where(
                ProcurementCase.status.in_(list(ACTIVE_CASE_STATUSES)),
                or_(
                    ProcurementCase.current_agent_id == AGENT_ID,
                    ProcurementCase.status.in_(list(MANAGER_QUEUE_STATUSES)),
                ),
                ProcurementCase.closed_at.is_(None),
            )
            .order_by(ProcurementCase.updated_at.desc())
        )
        return list(result)

    @staticmethod
    def _quote_unit_prices(workspace: dict[str, Any]) -> dict[str, Decimal]:
        prices: dict[str, Decimal] = {}
        for quote in workspace.get("quotes") or []:
            if not isinstance(quote, dict):
                continue
            for line in quote.get("lines") or []:
                if not isinstance(line, dict):
                    continue
                line_id = str(line.get("line_id") or "").strip()
                unit_price = line.get("unit_price")
                if not line_id or unit_price is None:
                    continue
                prices.setdefault(line_id, Decimal(str(unit_price)))
        return prices

    @staticmethod
    def _public_workspace(workspace: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "lifecycle_state",
            "agent_stage",
            "paused_for_human",
            "agent_interrupt",
            "kpi_flags",
            "evaluation",
            "suppliers",
            "supplier_searches",
            "quotes",
            "comparison",
            "rfq_drafts",
            "purchase_order_drafts",
            "approvals",
            "shipment_events",
            "payment_document_draft",
            "recommendation",
            "recommendation_audit",
            "operations",
            "nonconformities",
            "supplier_graph",
            "line_amounts",
        )
        list_keys = {
            "suppliers",
            "supplier_searches",
            "quotes",
            "rfq_drafts",
            "purchase_order_drafts",
            "approvals",
            "shipment_events",
            "recommendation_audit",
            "operations",
            "nonconformities",
        }
        dict_keys = {"line_amounts", "kpi_flags", "evaluation", "agent_interrupt"}
        payload = {
            key: workspace.get(
                key,
                []
                if key in list_keys
                else {}
                if key in dict_keys
                else False
                if key == "paused_for_human"
                else None,
            )
            for key in keys
        }
        return payload

    @staticmethod
    def _supplier_query_from_case(case: ProcurementCase) -> str:
        values = [
            position.nomenclature_name or position.nomenclature_id
            for position in case.positions or []
            if not position.cancelled
        ]
        query = ", ".join(dict.fromkeys(value for value in values if value))
        return query[:500] or case.source_number or str(case.id)

    @staticmethod
    def _supplier_category_from_case(case: ProcurementCase) -> str | None:
        for position in case.positions or []:
            raw = position.raw_payload or {}
            category = raw.get("category") or raw.get("Категория")
            if category:
                return str(category)[:255]
        return None

    async def _event(
        self,
        case: ProcurementCase,
        event_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> None:
        exists = await self.db.scalar(
            select(ProcurementCaseEvent.id).where(
                ProcurementCaseEvent.case_id == case.id,
                ProcurementCaseEvent.idempotency_key == idempotency_key,
            )
        )
        if exists is None:
            self.db.add(
                ProcurementCaseEvent(
                    case_id=case.id,
                    correlation_id=case.correlation_id,
                    event_type=event_type,
                    agent_id=AGENT_ID,
                    actor_role="procurement_manager",
                    previous_status=case.status,
                    new_status=case.status,
                    idempotency_key=idempotency_key,
                    payload=jsonable_encoder(payload),
                )
            )
        await self.db.flush()


def build_default_rfq_lines(case: ProcurementCase) -> list[RFQLine]:
    return [
        RFQLine(
            line_id=position.line_id,
            nomenclature_id=position.nomenclature_id,
            description=position.nomenclature_name or position.nomenclature_id,
            quantity=position.quantity,
            unit=position.unit or "шт.",
            required_date=position.required_date.date() if position.required_date else None,
        )
        for position in case.positions or []
        if not position.cancelled
    ]


__all__ = [
    "AGENT_ID",
    "MANAGER_QUEUE_STATUSES",
    "ProcurementManagerService",
    "build_default_rfq_lines",
    "case_in_manager_queue",
]
