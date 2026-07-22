"""API ролевых агентов ОМТО: права по должности, KPI-дашборд, запуск.

Доступ к дашборду каждого агента ограничен должностью пользователя
(см. :mod:`app.services.omto_permission`): дашборд видит только человек, чья
должность соответствует названию агента, либо суперпользователь.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.agents.omto_role_agents.catalog import OMTO_AGENTS
from app.agents.omto_role_agents.schemas import OmtoAgentRequest, OmtoAgentResult
from app.models.user import User
from app.schemas.omto import (
    OmtoDashboardRead,
    OmtoHitlPending,
    OmtoPermissionsRead,
    OmtoResumeRequest,
    OmtoRunRequest,
    OmtoRunResultRead,
)
from app.services.omto_agent_service import resume_and_record, run_and_record
from app.services.omto_kpi import compute_dashboard
from app.services.omto_permission import (
    accessible_omto_agents,
    can_access_omto_agent,
)

router = APIRouter(prefix="/omto", tags=["omto"])


async def _require_agent_access(db: DbSession, user: User, slug: str) -> None:
    if slug not in OMTO_AGENTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Агент не найден")
    if not await can_access_omto_agent(db, user, slug):
        spec = OMTO_AGENTS[slug]
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Дашборд доступен только пользователю с должностью «{spec.position_role}»"
            ),
        )


@router.get("/me/permissions", response_model=OmtoPermissionsRead)
async def get_omto_permissions(
    db: DbSession,
    current_user: CurrentUser,
) -> OmtoPermissionsRead:
    return OmtoPermissionsRead(
        accessible_role_agents=await accessible_omto_agents(db, current_user),
        is_superuser=bool(current_user.is_superuser),
    )


@router.get("/role-agents/{slug}/dashboard", response_model=OmtoDashboardRead)
async def get_omto_dashboard(
    slug: str,
    db: DbSession,
    current_user: CurrentUser,
) -> OmtoDashboardRead:
    await _require_agent_access(db, current_user, slug)
    payload = await compute_dashboard(db, slug)
    return OmtoDashboardRead.model_validate(payload)


@router.post("/role-agents/{slug}/run", response_model=OmtoRunResultRead)
async def run_omto_role_agent(
    slug: str,
    data: OmtoRunRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> OmtoRunResultRead:
    await _require_agent_access(db, current_user, slug)
    correlation_id = data.correlation_id or f"omto-{uuid.uuid4()}"
    request = OmtoAgentRequest(
        correlation_id=correlation_id,
        tenant_id=data.tenant_id,
        task_type=data.task_type,
        requested_by=str(current_user.id),
        caller_agent_id="omto_orchestrator",
        task_payload=data.task_payload,
    )
    result = await run_and_record(
        db, slug, request, triggered_by_id=current_user.id
    )
    await db.commit()
    return _to_read(result)


@router.post("/role-agents/{slug}/resume", response_model=OmtoRunResultRead)
async def resume_omto_role_agent(
    slug: str,
    data: OmtoResumeRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> OmtoRunResultRead:
    """Возобновляет приостановленный на HITL запуск решением человека."""
    await _require_agent_access(db, current_user, slug)
    passed = data.passed if data.passed is not None else data.resolution == "approved"
    result = await resume_and_record(
        db,
        slug,
        thread_id=data.thread_id,
        decision={
            "resolution": data.resolution,
            "passed": passed,
            "comment": data.comment,
        },
        triggered_by_id=current_user.id,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Нет приостановленного запуска с указанным thread_id",
        )
    await db.commit()
    return _to_read(result)


def _to_read(result: OmtoAgentResult) -> OmtoRunResultRead:
    pending = result.output_data.get("hitl_pending")
    return OmtoRunResultRead(
        agent_id=result.agent_id,
        status=result.status,
        role_status=result.role_status,
        summary=result.summary,
        data_confidence=result.data_confidence.value,
        requires_human_review=result.requires_human_review,
        correlation_id=result.correlation_id,
        thread_id=result.correlation_id,
        task_type=result.task_type,
        wait_reason=result.wait_reason,
        hitl_pending=OmtoHitlPending.model_validate(pending) if pending else None,
        output_data=result.output_data,
    )


__all__ = ["router"]
