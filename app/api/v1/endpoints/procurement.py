from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession
from app.models.user import User
from app.schemas.procurement import (
    ProcurementCaseDetail,
    ProcurementCaseEventRead,
    ProcurementDashboardRead,
    ProcurementPermissionsRead,
    ProcurementRefreshResult,
    ProcurementRoleAgentResultRead,
    ProcurementRoleAgentResumeRequest,
    ProcurementSyncStatusRead,
)
from app.services.procurement_orchestrator_service import ProcurementOrchestratorService
from app.services.procurement_permission import (
    PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG,
    can_access_procurement_orchestrator,
    can_access_production_preparation_engineer,
    can_refresh_procurement_orchestrator,
)

router = APIRouter(prefix="/procurement", tags=["procurement"])


async def _require_superuser(db: DbSession, user: User) -> None:
    if not await can_access_procurement_orchestrator(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Оркестратор закупок доступен только администратору системы",
        )


async def _require_engineer_workspace(
    db: DbSession,
    user: User,
    agent_id: str,
) -> None:
    if agent_id != PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ролевой агент не найден")
    if not await can_access_production_preparation_engineer(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Рабочее место доступно только инженеру по подготовке производства",
        )


@router.get("/me/permissions", response_model=ProcurementPermissionsRead)
async def get_procurement_permissions(
    db: DbSession,
    current_user: CurrentUser,
) -> ProcurementPermissionsRead:
    can_access = await can_access_procurement_orchestrator(db, current_user)
    can_access_engineer = await can_access_production_preparation_engineer(db, current_user)
    return ProcurementPermissionsRead(
        can_access_orchestrator=can_access,
        can_access_role_workspace=can_access_engineer,
        accessible_role_agents=(
            [PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG] if can_access_engineer else []
        ),
        can_submit_role_result=False,
        can_refresh=can_access and await can_refresh_procurement_orchestrator(db, current_user),
        is_superuser=bool(current_user.is_superuser),
    )


@router.get(
    "/role-agents/{agent_id}/dashboard",
    response_model=ProcurementDashboardRead,
)
async def get_procurement_role_dashboard(
    agent_id: str,
    db: DbSession,
    current_user: CurrentUser,
    view: Literal["active", "processing", "archive"] = Query(default="active"),
) -> ProcurementDashboardRead:
    await _require_engineer_workspace(db, current_user, agent_id)
    payload = await ProcurementOrchestratorService(db, enqueue_case=False).list_dashboard(
        view=view,
        source_type="production_material_order",
    )
    return ProcurementDashboardRead.model_validate(payload)


@router.get(
    "/role-agents/{agent_id}/cases/{case_id}",
    response_model=ProcurementCaseDetail,
)
async def get_procurement_role_case(
    agent_id: str,
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> ProcurementCaseDetail:
    await _require_engineer_workspace(db, current_user, agent_id)
    payload = await ProcurementOrchestratorService(db, enqueue_case=False).get_case(case_id)
    if payload is None or payload.get("source_type") != "production_material_order":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кейс не найден")
    metadata = payload.get("case_metadata") or {}
    payload["case_metadata"] = {
        "production_order_1c_ref": metadata.get("production_order_1c_ref"),
        "production_order_type": metadata.get("production_order_type"),
        "production_preparation_engineer_output": metadata.get(
            "production_preparation_engineer_output"
        ),
        "engineer_evidence_fingerprint": metadata.get("engineer_evidence_fingerprint"),
        "engineer_calculated_at": metadata.get("engineer_calculated_at"),
    }
    payload["assigned_agents"] = [PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG]
    payload["route_stages"] = []
    payload["events"] = [
        event
        for event in payload.get("events") or []
        if event.get("agent_id") == PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG
        or event.get("event_type")
        in {
            "case_created_from_source",
            "source_document_changed",
            "case_archived_from_source",
        }
    ]
    payload["timeline"] = [
        item
        for item in payload.get("timeline") or []
        if item.get("actor_id") == PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG
        or item.get("kind")
        in {
            "case_created_from_source",
            "source_document_changed",
            "case_archived_from_source",
        }
    ]
    return ProcurementCaseDetail.model_validate(payload)


@router.get("/dashboard", response_model=ProcurementDashboardRead)
async def get_procurement_dashboard(
    db: DbSession,
    current_user: CurrentUser,
    view: Literal["active", "processing", "archive"] = Query(default="active"),
) -> ProcurementDashboardRead:
    await _require_superuser(db, current_user)
    service = ProcurementOrchestratorService(db, enqueue_case=False)
    payload = await service.list_dashboard(view=view)
    return ProcurementDashboardRead.model_validate(payload)


@router.get("/cases", response_model=ProcurementDashboardRead)
async def list_procurement_cases(
    db: DbSession,
    current_user: CurrentUser,
    view: Literal["active", "processing", "archive"] = Query(default="processing"),
) -> ProcurementDashboardRead:
    return await get_procurement_dashboard(db=db, current_user=current_user, view=view)


@router.get("/cases/{case_id}", response_model=ProcurementCaseDetail)
async def get_procurement_case(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> ProcurementCaseDetail:
    await _require_superuser(db, current_user)
    service = ProcurementOrchestratorService(db, enqueue_case=False)
    payload = await service.get_case(case_id)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кейс не найден")
    return ProcurementCaseDetail.model_validate(payload)


@router.get("/cases/{case_id}/events", response_model=list[ProcurementCaseEventRead])
async def list_procurement_case_events(
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> list[ProcurementCaseEventRead]:
    await _require_superuser(db, current_user)
    service = ProcurementOrchestratorService(db, enqueue_case=False)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кейс не найден")
    events = await service.list_case_events(case_id)
    return [ProcurementCaseEventRead.model_validate(item) for item in events]


@router.post(
    "/cases/{case_id}/agent-result",
    response_model=ProcurementRoleAgentResultRead,
)
async def resume_procurement_role_agent(
    case_id: uuid.UUID,
    data: ProcurementRoleAgentResumeRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> ProcurementRoleAgentResultRead:
    await _require_superuser(db, current_user)
    service = ProcurementOrchestratorService(db, enqueue_case=False)
    result = await service.resume_case_agent(
        case_id,
        data.model_dump(mode="json"),
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="У кейса нет ожидающей задачи ролевого агента",
        )
    await db.commit()
    return ProcurementRoleAgentResultRead.model_validate(result)


@router.get("/sync-status", response_model=list[ProcurementSyncStatusRead])
async def get_procurement_sync_status(
    db: DbSession,
    current_user: CurrentUser,
) -> list[ProcurementSyncStatusRead]:
    await _require_superuser(db, current_user)
    service = ProcurementOrchestratorService(db, enqueue_case=False)
    payload = await service.list_sync_status()
    return [ProcurementSyncStatusRead.model_validate(item) for item in payload]


@router.post("/refresh", response_model=ProcurementRefreshResult)
async def refresh_procurement_sources(
    db: DbSession,
    current_user: CurrentUser,
) -> ProcurementRefreshResult:
    await _require_superuser(db, current_user)
    if not await can_refresh_procurement_orchestrator(db, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")

    from app.workers.tasks import poll_procurement_sources

    async_result = poll_procurement_sources.apply_async(queue="procurement_poll")
    return ProcurementRefreshResult(
        status="accepted",
        summary={"celery_task_id": async_result.id},
    )
