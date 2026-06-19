from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query, status

from app.agents.tasks_agent.config import DEFAULT_PORUCHENIYA_LIMIT
from app.api.deps import CurrentUser, DbSession
from app.schemas.porucheniya import (
    TasksDashboardRead,
    TasksDashboardRefreshRequest,
    TasksPermissionsRead,
)
from app.services.tasks_dashboard_service import (
    TasksDashboardService,
    TasksDashboardServiceError,
)
from app.services.tasks_permission import can_access_tasks_agent

router = APIRouter(prefix="/porucheniya", tags=["porucheniya"])


async def _require_agent_access(db: DbSession, user: CurrentUser) -> None:
    if not await can_access_tasks_agent(db, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к агенту контроля поручений",
        )


@router.get("/me/permissions", response_model=TasksPermissionsRead)
async def tasks_permissions(db: DbSession, current_user: CurrentUser) -> TasksPermissionsRead:
    return TasksPermissionsRead(
        can_access_agent=await can_access_tasks_agent(db, current_user),
    )


@router.get("/dashboard", response_model=TasksDashboardRead)
async def get_tasks_dashboard(
    db: DbSession,
    current_user: CurrentUser,
    period_start: str | None = Query(None, description="Начало периода YYYY-MM-DD (по умолчанию — вчера)"),
    period_end: str | None = Query(None, description="Конец периода YYYY-MM-DD (по умолчанию — вчера)"),
    limit: int = Query(DEFAULT_PORUCHENIYA_LIMIT, ge=1, le=1000),
) -> TasksDashboardRead:
    await _require_agent_access(db, current_user)
    try:
        return await TasksDashboardService(db).load_dashboard(
            current_user,
            period_start=period_start,
            period_end=period_end,
            limit=limit,
        )
    except TasksDashboardServiceError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post("/dashboard/refresh", response_model=TasksDashboardRead)
async def refresh_tasks_dashboard(
    db: DbSession,
    current_user: CurrentUser,
    body: TasksDashboardRefreshRequest = Body(default_factory=TasksDashboardRefreshRequest),
) -> TasksDashboardRead:
    await _require_agent_access(db, current_user)
    try:
        return await TasksDashboardService(db).load_dashboard(
            current_user,
            period_start=body.period_start,
            period_end=body.period_end,
            limit=body.limit,
        )
    except TasksDashboardServiceError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
