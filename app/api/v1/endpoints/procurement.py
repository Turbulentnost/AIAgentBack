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
    can_access_procurement_orchestrator,
    can_refresh_procurement_orchestrator,
)

router = APIRouter(prefix="/procurement", tags=["procurement"])


async def _require_superuser(db: DbSession, user: User) -> None:
    if not await can_access_procurement_orchestrator(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Оркестратор закупок доступен только администратору системы",
        )


@router.get("/me/permissions", response_model=ProcurementPermissionsRead)
async def get_procurement_permissions(
    db: DbSession,
    current_user: CurrentUser,
) -> ProcurementPermissionsRead:
    can_access = await can_access_procurement_orchestrator(db, current_user)
    return ProcurementPermissionsRead(
        can_access_orchestrator=can_access,
        can_refresh=can_access and await can_refresh_procurement_orchestrator(db, current_user),
        is_superuser=bool(current_user.is_superuser),
    )


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
