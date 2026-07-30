from __future__ import annotations

import uuid
from decimal import Decimal
from io import BytesIO
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.agents.procurement_manager_agent.schemas import (
    AgentResumeRequest,
    AgentRunRequest,
    AgentStatus,
    AllocationResult,
    AllPositionsResponse,
    ApprovalRecord,
    ApprovalRequest,
    ComparisonWeights,
    FulfillmentStatusUpdateRequest,
    LineAmountsUpdateRequest,
    LineScheduleUpdateRequest,
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
    ShipmentEventRequest,
    StrategyResumeRequest,
    StrategyRunRequest,
    StrategyStatus,
    Supplier,
    SupplierOffersResponse,
    SupplierQuote,
    SupplierSearchRequest,
    SupplierSearchResult,
    WorkspaceSummary,
)
from app.agents.procurement_manager_agent.service import (
    AGENT_ID,
    ProcurementManagerService,
    case_in_manager_queue,
)
from app.agents.procurement_manager_agent.suppliers import HybridSupplierSearchService
from app.api.deps import CurrentUser, DbSession, oauth2_scheme, resolve_user_from_token
from app.db.session import AsyncSessionLocal
from app.services.procurement_orchestrator_service import ProcurementOrchestratorService
from app.services.procurement_permission import (
    can_access_procurement_manager,
    can_refresh_procurement_orchestrator,
)
router = APIRouter(
    prefix=f"/procurement/role-agents/{AGENT_ID}",
    tags=["procurement-manager"],
)
operations_router = APIRouter(prefix="/procurement/operations", tags=["procurement-manager"])

# Dashboard cards previously embedded full workspace (~500KB×N) and froze the UI.
_DASHBOARD_DROP_KEYS = (
    "suppliers",
    "quotes",
    "rfq_drafts",
    "approvals",
    "shipment_events",
    "payment_document_draft",
    "recommendation",
    "recommendation_audit",
    "comparison",
)
_DASHBOARD_PM_DROP_KEYS = (
    *_DASHBOARD_DROP_KEYS,
    "supplier_searches",
    "nomenclature_results",
    "purchase_order_drafts",
    "operations",
    "nonconformities",
    "supplier_graph",
    "supply_policy",
    "strategy_graph",
    "strategy_artifact",
    "cost_estimate",
    "evaluation",
    "line_amounts",
    "agent_interrupt",
)
_CASE_DETAIL_DROP_KEYS = (
    "case_metadata",
    "events",
    "strategy_artifact",
    "supply_policy",
    "cost_estimate",
)
_TIMELINE_MAX = 40


def _slim_manager_dashboard_case(item: dict[str, Any]) -> dict[str, Any]:
    for key in _DASHBOARD_DROP_KEYS:
        item.pop(key, None)
    pm = item.get("procurement_manager")
    if isinstance(pm, dict):
        item["procurement_manager"] = {
            key: value for key, value in pm.items() if key not in _DASHBOARD_PM_DROP_KEYS
        }
    return item


def _slim_manager_case_detail(payload: dict[str, Any]) -> dict[str, Any]:
    for key in _CASE_DETAIL_DROP_KEYS:
        payload.pop(key, None)
    timeline = payload.get("timeline")
    if isinstance(timeline, list) and len(timeline) > _TIMELINE_MAX:
        payload["timeline"] = timeline[-_TIMELINE_MAX:]
    pm = payload.get("procurement_manager")
    if isinstance(pm, dict):
        for key in ("strategy_artifact", "supply_policy", "cost_estimate"):
            pm.pop(key, None)
    for key in ("strategy_artifact", "supply_policy", "cost_estimate"):
        payload.pop(key, None)
    return payload


async def _require_access(db: DbSession, user: CurrentUser) -> None:
    if not await can_access_procurement_manager(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Рабочее место доступно только менеджеру по закупкам / ОМТО",
        )


async def _commit(db: DbSession) -> None:
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise


@router.get("/dashboard")
async def dashboard(
    db: DbSession,
    current_user: CurrentUser,
    view: Literal["active", "processing", "archive"] = Query(default="processing"),
) -> dict[str, Any]:
    await _require_access(db, current_user)
    payload = await ProcurementOrchestratorService(db, enqueue_case=False).list_dashboard(
        view=view,
        purchase_manager_workspace=True,
    )
    for group in payload.get("groups", []):
        cases = [
            item
            for item in group.get("cases", [])
            if case_in_manager_queue(
                current_agent_id=item.get("current_agent_id"),
                status=item.get("status"),
                purchase_manager_invoked_at=item.get("purchase_manager_invoked_at"),
                supplier_coverage_status=item.get("supplier_coverage_status"),
            )
        ]
        group["cases"] = cases
        group["cases_count"] = len(cases)
    payload["total_cases"] = sum(
        len(group.get("cases", [])) for group in payload.get("groups", [])
    )
    enriched = await ProcurementManagerService(db).enrich_dashboard_cases(payload)
    for group in enriched.get("groups", []):
        group["cases"] = [
            _slim_manager_dashboard_case(item) for item in group.get("cases") or []
        ]
    return enriched


@router.get("/workspace-summary", response_model=WorkspaceSummary)
async def workspace_summary(
    db: DbSession,
    current_user: CurrentUser,
) -> WorkspaceSummary:
    await _require_access(db, current_user)
    return await ProcurementManagerService(db).workspace_summary()


@router.get("/material-bank", response_model=MaterialBankResponse)
async def material_bank(
    db: DbSession,
    current_user: CurrentUser,
) -> MaterialBankResponse:
    await _require_access(db, current_user)
    return await ProcurementManagerService(db).material_bank()


async def _allocation_result(db: DbSession) -> AllocationResult:
    result = await ProcurementManagerService(db).allocate_coverage()
    return AllocationResult.model_validate(
        {
            "cases": result.get("cases") or [],
            "lines": result.get("lines") or [],
            "by_nomenclature": result.get("by_nomenclature") or [],
            "summary": result.get("summary") or {},
            "price_formula": result.get("price_formula"),
        }
    )


@router.get("/coverage", response_model=AllocationResult)
async def coverage(
    db: DbSession,
    current_user: CurrentUser,
) -> AllocationResult:
    await _require_access(db, current_user)
    return await _allocation_result(db)


@router.post("/allocate", response_model=AllocationResult)
async def allocate(
    db: DbSession,
    current_user: CurrentUser,
) -> AllocationResult:
    await _require_access(db, current_user)
    return await _allocation_result(db)


@router.get("/all-positions", response_model=AllPositionsResponse)
async def all_positions(
    db: DbSession,
    current_user: CurrentUser,
) -> AllPositionsResponse:
    """Aggregated nomenclature for «Все позиции»: price_min/max + estimated_amount."""
    await _require_access(db, current_user)
    return await ProcurementManagerService(db).all_positions()


@router.get(
    "/cases/{case_id}/supplier-offers",
    response_model=SupplierOffersResponse,
)
async def supplier_offers(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    nomenclature: str = Query(..., min_length=1, max_length=128),
    need_qty: Decimal | None = Query(default=None, ge=0),
    top_n: int = Query(default=3, ge=1, le=10),
) -> SupplierOffersResponse:
    """Top-N supplier offers ranked by price + coverage utility for a nomenclature."""
    await _require_access(db, current_user)
    try:
        return await ProcurementManagerService(db).supplier_offers_for_case(
            case_id,
            nomenclature_id=nomenclature,
            need_qty=need_qty,
            top_n=top_n,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.put("/cases/{case_id}/line-amounts")
async def update_line_amounts(
    case_id: uuid.UUID,
    data: LineAmountsUpdateRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> dict[str, Any]:
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).save_line_amounts(case_id, data)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await _commit(db)
    return result


@router.post("/cases/{case_id}/otk-presentation")
async def create_otk_presentation(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> dict[str, Any]:
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).create_otk_presentation_from_case(
            case_id
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await _commit(db)
    return result


@router.patch("/cases/{case_id}/fulfillment-status")
async def update_fulfillment_status(
    case_id: uuid.UUID,
    data: FulfillmentStatusUpdateRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> dict[str, Any]:
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).update_fulfillment_status(case_id, data)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await _commit(db)
    return result


@router.patch("/cases/{case_id}/lines/{line_id}/schedule")
async def update_line_schedule(
    case_id: uuid.UUID,
    line_id: str,
    data: LineScheduleUpdateRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> dict[str, Any]:
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).update_line_schedule(
            case_id, line_id, data
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await _commit(db)
    return result


def _attachment_content_disposition(filename: str) -> str:
    """Build Content-Disposition that Starlette can encode as latin-1.

    Non-ASCII source numbers (e.g. ЗП-DEMO-0024) must not appear in the
    ``filename=`` fallback — only in RFC 5987 ``filename*=``.
    """
    safe_name = filename.replace('"', "").strip() or "download.bin"
    encoded = quote(safe_name)
    ascii_name = safe_name.encode("ascii", "ignore").decode("ascii")
    # Collapse gaps left by stripped Cyrillic: estimate_ЗП-DEMO → estimate_-DEMO.
    ascii_name = ascii_name.replace("_-", "_").replace("-_", "-")
    while "__" in ascii_name:
        ascii_name = ascii_name.replace("__", "_")
    ascii_name = ascii_name.strip("._- ") or "download.bin"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'


@router.get("/cases/{case_id}/estimate-report")
async def estimate_report(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> StreamingResponse:
    await _require_access(db, current_user)
    service = ProcurementManagerService(db)
    try:
        case = await service.require_case(case_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    try:
        content = service.build_estimate_xlsx(case)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Не удалось сформировать Excel-смету: {exc}",
        ) from exc
    filename = f"estimate_{case.source_number or case_id}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _attachment_content_disposition(filename)},
    )


@router.post("/sync-from-1c")
async def sync_from_1c(
    db: DbSession,
    current_user: CurrentUser,
    case_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict[str, Any]:
    """Manual 1C sync (force=true). Optional case_id enriches one case chain only."""
    await _require_access(db, current_user)
    if case_id is not None:
        from app.services.procurement_1c_chain_enricher import Procurement1CChainEnricher

        enricher = Procurement1CChainEnricher(db)
        result = await enricher.enrich_case_by_id(case_id, force=True)
        await db.commit()
        return {
            "status": "accepted",
            "mode": "case_enrich",
            "summary": result,
        }
    if await can_refresh_procurement_orchestrator(db, current_user):
        from app.workers.tasks import sync_procurement_material_orders

        async_result = sync_procurement_material_orders.apply_async(
            queue="procurement_poll",
        )
        return {
            "status": "accepted",
            "mode": "poll",
            "summary": {"celery_task_id": async_result.id, "force": True},
        }
    return {
        "status": "local_refresh",
        "mode": "cache",
        "summary": {
            "message": (
                "Полный опрос 1С доступен администратору. "
                "Обновлены локальные данные рабочего места."
            )
        },
    }


@router.get("/cases/{case_id}")
async def case_detail(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> dict[str, Any]:
    await _require_access(db, current_user)
    payload = await ProcurementOrchestratorService(db, enqueue_case=False).get_case(case_id)
    if payload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Кейс не найден")
    workspace = await ProcurementManagerService(db).workspace_payload(case_id)
    payload["procurement_manager"] = workspace
    payload.update(workspace)
    return _slim_manager_case_detail(payload)


@router.post("/cases/{case_id}/supplier-search", response_model=SupplierSearchResult)
async def supplier_search(
    case_id: uuid.UUID,
    token: Annotated[str | None, Depends(oauth2_scheme)],
    data: Annotated[SupplierSearchRequest | None, Body()] = None,
) -> SupplierSearchResult:
    """Phased search: short DB sessions around a long browser/Qwen wait.

    Holding one DbSession for the whole force_web search exhausted the pool and
    left the UI spinning on dashboard/case cards («Загрузка…»).
    """
    request = data or SupplierSearchRequest()
    async with AsyncSessionLocal() as db:
        user = await resolve_user_from_token(db, token)
        await _require_access(db, user)
        service = ProcurementManagerService(db)
        try:
            prepared = await service.prepare_supplier_search(case_id, request)
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        await db.commit()
        if isinstance(prepared, SupplierSearchResult):
            return prepared

    # No DB checkout while Edge/Qwen runs (can take minutes).
    runner = object.__new__(ProcurementManagerService)
    runner.db = None  # type: ignore[assignment]
    runner.supplier_search = HybridSupplierSearchService()
    runner.use_graph = True
    execute_error: str | None = None
    result: SupplierSearchResult | None = None
    try:
        result = await runner.execute_supplier_search_web(prepared)
    except Exception as exc:
        execute_error = str(exc)[:1000]

    async with AsyncSessionLocal() as db:
        service = ProcurementManagerService(db)
        try:
            if execute_error is not None:
                await service.finalize_supplier_search(
                    prepared, execute_error=execute_error
                )
            assert result is not None
            finalized = await service.finalize_supplier_search(prepared, result)
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)
            ) from exc
        await db.commit()
        return finalized


@router.get("/cases/{case_id}/supplier-search/progress/{operation_id}")
async def supplier_search_progress(
    case_id: uuid.UUID,
    operation_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> dict[str, Any]:
    """Live Qwen/web-search thoughts for an in-flight supplier search.

    Reads the short-lived in-memory buffer (works while the long POST is still
    uncommitted). Soft: empty thoughts when the buffer has nothing yet.
    """
    await _require_access(db, current_user)
    # Ensure the case exists for the caller (404 vs empty thoughts).
    try:
        await ProcurementManagerService(db).require_case(case_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return ProcurementManagerService.supplier_search_progress(
        case_id=str(case_id),
        operation_id=operation_id,
    )


@router.post(
    "/cases/{case_id}/supplier-search/enrich",
    response_model=SupplierSearchResult,
)
async def supplier_search_enrich(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> SupplierSearchResult:
    """Re-enrich stored web supplier cards by fetching product pages."""
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).enrich_web_supplier_cards(case_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await _commit(db)
    return result


@router.post("/strategy/run", response_model=StrategyStatus)
async def strategy_run(
    db: DbSession,
    current_user: CurrentUser,
    data: Annotated[StrategyRunRequest | None, Body()] = None,
) -> StrategyStatus:
    """Queue-level supply strategy: waves → bank → optimize → HITL → multi-PO drafts."""
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).strategy_run(data)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        detail = str(exc).strip() or repr(exc)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Не удалось запустить стратегию поставок: {detail}",
        ) from exc
    await _commit(db)
    return result


@router.post("/strategy/resume", response_model=StrategyStatus)
async def strategy_resume(
    data: StrategyResumeRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> StrategyStatus:
    """HITL resume for supply policy / shortlist or multi-PO order drafts."""
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).strategy_resume(data)
    except KeyError as exc:
        detail = str(exc).strip() or repr(exc)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Не удалось продолжить стратегию: {detail}",
        ) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        detail = str(exc).strip() or repr(exc)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Не удалось продолжить стратегию: {detail}",
        ) from exc
    await _commit(db)
    return result


@router.get("/strategy/status", response_model=StrategyStatus)
async def strategy_status(
    db: DbSession,
    current_user: CurrentUser,
) -> StrategyStatus:
    """Waves, supply_policy, estimates and multi-PO drafts for the manager queue."""
    await _require_access(db, current_user)
    return await ProcurementManagerService(db).strategy_status()


@router.post("/cases/{case_id}/agent/run", response_model=AgentStatus)
async def agent_run(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    data: Annotated[AgentRunRequest | None, Body()] = None,
) -> AgentStatus:
    """Start (or idempotently replay) the full procurement manager agent graph."""
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).agent_run(case_id, data)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        detail = str(exc).strip() or repr(exc)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Не удалось запустить агента: {detail}",
        ) from exc
    await _commit(db)
    return result


@router.post("/cases/{case_id}/agent/resume", response_model=AgentStatus)
async def agent_resume(
    case_id: uuid.UUID,
    data: AgentResumeRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> AgentStatus:
    """HITL resume: approve_shortlist / approve_rfq_draft / approve_order_draft / reject."""
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).agent_resume(case_id, data)
    except KeyError as exc:
        # KeyError is a LookupError; must not be mapped to bare 404 "'request'".
        detail = str(exc).strip() or repr(exc)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Не удалось продолжить агента: {detail}",
        ) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        detail = str(exc).strip() or repr(exc)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Не удалось продолжить агента: {detail}",
        ) from exc
    await _commit(db)
    return result


@router.get("/cases/{case_id}/agent/status", response_model=AgentStatus)
async def agent_status(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> AgentStatus:
    await _require_access(db, current_user)
    try:
        return await ProcurementManagerService(db).agent_status(case_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/cases/{case_id}/supplier-graph/resume", response_model=AgentStatus)
async def resume_supplier_graph(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    data: Annotated[dict[str, Any] | None, Body()] = None,
) -> AgentStatus:
    """Legacy alias for agent/resume (approve_shortlist / approve_rfq_draft / reject / approve_order_draft)."""
    await _require_access(db, current_user)
    decision = dict(data or {})
    try:
        resume = AgentResumeRequest.model_validate(decision)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "action must be approve_shortlist, approve_rfq_draft, approve_order_draft, or reject",
        ) from exc
    try:
        result = await ProcurementManagerService(db).agent_resume(case_id, resume)
    except KeyError as exc:
        detail = str(exc).strip() or repr(exc)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Не удалось продолжить агента: {detail}",
        ) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await _commit(db)
    return result


@router.get(
    "/cases/{case_id}/purchase-order-drafts",
    response_model=list[PurchaseOrderDraft],
)
async def list_purchase_order_drafts(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> list[PurchaseOrderDraft]:
    await _require_access(db, current_user)
    try:
        return await ProcurementManagerService(db).list_purchase_order_drafts(case_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get(
    "/cases/{case_id}/purchase-order-drafts/{po_id}",
    response_model=PurchaseOrderDraft,
)
async def get_purchase_order_draft(
    case_id: uuid.UUID,
    po_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> PurchaseOrderDraft:
    await _require_access(db, current_user)
    try:
        return await ProcurementManagerService(db).get_purchase_order_draft(case_id, po_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "/cases/{case_id}/purchase-order-drafts",
    response_model=PurchaseOrderDraft,
    status_code=status.HTTP_201_CREATED,
)
async def create_purchase_order_draft(
    case_id: uuid.UUID,
    data: PurchaseOrderDraftRequest,
    db: DbSession,
    current_user: CurrentUser,
    approval_id: Annotated[str | None, Query()] = None,
) -> PurchaseOrderDraft:
    """Create draft-only purchase order (executed=false, payment forbidden)."""
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).create_purchase_order_draft(
            case_id,
            data,
            approval_id=approval_id,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await _commit(db)
    return result


@router.get("/cases/{case_id}/suppliers", response_model=list[Supplier])
async def suppliers(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> list[Supplier]:
    await _require_access(db, current_user)
    try:
        return await ProcurementManagerService(db).list_suppliers(case_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/cases/{case_id}/rfq-drafts", response_model=list[RFQDraft])
async def rfq_drafts(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> list[RFQDraft]:
    await _require_access(db, current_user)
    try:
        return await ProcurementManagerService(db).list_rfq_drafts(case_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "/cases/{case_id}/rfq-drafts",
    response_model=RFQDraft,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/cases/{case_id}/rfqs/draft",
    response_model=RFQDraft,
    status_code=status.HTTP_201_CREATED,
)
async def create_rfq_draft(
    case_id: uuid.UUID,
    data: RFQDraftRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> RFQDraft:
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).create_rfq_draft(case_id, data)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await _commit(db)
    return result


@router.get("/cases/{case_id}/quotes", response_model=list[SupplierQuote])
async def quotes(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> list[SupplierQuote]:
    await _require_access(db, current_user)
    try:
        return await ProcurementManagerService(db).list_quotes(case_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "/cases/{case_id}/quotes",
    response_model=SupplierQuote,
    status_code=status.HTTP_201_CREATED,
)
async def submit_quote(
    case_id: uuid.UUID,
    data: QuoteSubmission,
    db: DbSession,
    current_user: CurrentUser,
) -> SupplierQuote:
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).submit_quote(case_id, data)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await _commit(db)
    return result


@router.get("/cases/{case_id}/comparison", response_model=QuoteComparison)
async def comparison(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    price_weight: Annotated[Decimal, Query(ge=0)] = Decimal("0.45"),
    delivery_weight: Annotated[Decimal, Query(ge=0)] = Decimal("0.25"),
    quality_weight: Annotated[Decimal, Query(ge=0)] = Decimal("0.20"),
    risk_weight: Annotated[Decimal, Query(ge=0)] = Decimal("0.10"),
) -> QuoteComparison:
    await _require_access(db, current_user)
    weights = ComparisonWeights(
        price=price_weight,
        delivery=delivery_weight,
        quality=quality_weight,
        risk=risk_weight,
    )
    try:
        result = await ProcurementManagerService(db).comparison(case_id, weights)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await _commit(db)
    return result


@router.post(
    "/cases/{case_id}/recommendation",
    response_model=RecommendationRecord,
    status_code=status.HTTP_201_CREATED,
)
async def create_recommendation(
    case_id: uuid.UUID,
    data: RecommendationRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> RecommendationRecord:
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).recommendation(case_id, data)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await _commit(db)
    return result


@router.get("/cases/{case_id}/recommendation")
async def get_recommendation(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> dict[str, Any]:
    await _require_access(db, current_user)
    try:
        workspace = await ProcurementManagerService(db).workspace_payload(case_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return workspace.get("recommendation") or {
        "status": "not_created",
        "requires_human_approval": True,
        "payment_execution_allowed": False,
    }


@router.post("/cases/{case_id}/approvals", response_model=ApprovalRecord)
async def record_approval(
    case_id: uuid.UUID,
    data: ApprovalRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> ApprovalRecord:
    await _require_access(db, current_user)
    if data.status in {"approved", "rejected"} and not current_user.is_superuser:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Решение по согласованию может зафиксировать только уполномоченный пользователь",
        )
    try:
        result = await ProcurementManagerService(db).record_approval(
            case_id,
            data,
            actor_user_id=str(current_user.id),
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await _commit(db)
    return result


@router.get("/cases/{case_id}/approvals", response_model=list[ApprovalRecord])
async def approvals(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> list[ApprovalRecord]:
    await _require_access(db, current_user)
    try:
        return await ProcurementManagerService(db).list_approvals(case_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/cases/{case_id}/shipment-events")
async def record_shipment_event(
    case_id: uuid.UUID,
    data: ShipmentEventRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> dict[str, Any]:
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).record_shipment(case_id, data)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await _commit(db)
    return result


@router.get("/cases/{case_id}/shipment-events")
async def shipment_events(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> list[dict[str, Any]]:
    await _require_access(db, current_user)
    try:
        return await ProcurementManagerService(db).list_shipment_events(case_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/cases/{case_id}/nonconformity")
async def nonconformity(
    case_id: uuid.UUID,
    data: NonconformityRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> dict[str, Any]:
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).handoff_nonconformity(case_id, data)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    orchestrator = ProcurementOrchestratorService(db, enqueue_case=True)
    await orchestrator.dispatch_case(case_id)
    await _commit(db)
    if orchestrator.pending_dispatches:
        from app.workers.tasks import run_procurement_case_task

        for queued_case_id, task_id in orchestrator.pending_dispatches:
            run_procurement_case_task.apply_async(
                args=[queued_case_id, task_id],
                queue="agents",
            )
    return result


@router.get(
    "/cases/{case_id}/operations/{operation_id}",
    response_model=OperationStatus,
)
async def operation_status(
    case_id: uuid.UUID,
    operation_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> OperationStatus:
    await _require_access(db, current_user)
    try:
        result = await ProcurementManagerService(db).operation_status(case_id, operation_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Операция не найдена")
    return result


async def _global_operation_status(
    operation_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> OperationStatus:
    await _require_access(db, current_user)
    result = await ProcurementManagerService(db).global_operation_status(operation_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Операция не найдена")
    return result


@router.get("/operations/{operation_id}", response_model=OperationStatus)
async def agent_operation_status(
    operation_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> OperationStatus:
    return await _global_operation_status(operation_id, db, current_user)


@operations_router.get("/{operation_id}", response_model=OperationStatus)
async def global_operation_status(
    operation_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> OperationStatus:
    return await _global_operation_status(operation_id, db, current_user)

