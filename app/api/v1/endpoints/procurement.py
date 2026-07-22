from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.agents.quality_kpi_agent.service import QualityKpiService
from app.api.deps import CurrentUser, DbSession
from app.models.enums import ProcurementCaseStatus
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
    OMTO_SUPPORT_MANAGER_AGENT_SLUG,
    OTK_HEAD_AGENT_SLUG,
    PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG,
    QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG,
    QUALITY_ENGINEER_AGENT_SLUG,
    QUALITY_KPI_AGENT_SLUG,
    can_access_omto_support_manager,
    can_access_otk_head,
    can_access_procurement_orchestrator,
    can_access_production_preparation_engineer,
    can_access_quality_deputy_director,
    can_access_quality_engineer,
    can_access_quality_kpi,
    can_refresh_procurement_orchestrator,
)

router = APIRouter(prefix="/procurement", tags=["procurement"])

_ROLE_WORKSPACE_FORBIDDEN = {
    PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG: (
        "Рабочее место доступно только инженеру по подготовке производства"
    ),
    OMTO_SUPPORT_MANAGER_AGENT_SLUG: (
        "Рабочее место доступно только менеджеру по сопровождению ОМТО"
    ),
    OTK_HEAD_AGENT_SLUG: "Рабочее место доступно только начальнику ОТК",
    QUALITY_ENGINEER_AGENT_SLUG: "Рабочее место доступно только инженеру по качеству",
    QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG: (
        "Рабочее место доступно только заместителю директора по качеству"
    ),
    QUALITY_KPI_AGENT_SLUG: "Рабочее место KPI доступно администратору / ЗДК",
}

_QUALITY_CASE_STATUSES = {
    ProcurementCaseStatus.QUALITY_QUEUED.value,
    ProcurementCaseStatus.QUALITY_ASSIGNED.value,
    ProcurementCaseStatus.QUALITY_DOC_CHECK.value,
    ProcurementCaseStatus.QUALITY_INSPECTION.value,
    ProcurementCaseStatus.QUALITY_DECISION.value,
    ProcurementCaseStatus.ISOLATED.value,
    ProcurementCaseStatus.NONCONFORMITY.value,
    ProcurementCaseStatus.REWORK.value,
    ProcurementCaseStatus.REINSPECTION.value,
    ProcurementCaseStatus.QUALITY_RELEASED.value,
    ProcurementCaseStatus.AGENT_WAITING.value,
}


async def _require_superuser(db: DbSession, user: User) -> None:
    if not await can_access_procurement_orchestrator(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Оркестратор закупок доступен только администратору системы",
        )


async def _role_access(db: DbSession, user: User, agent_id: str) -> bool:
    checkers = {
        PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG: can_access_production_preparation_engineer,
        OMTO_SUPPORT_MANAGER_AGENT_SLUG: can_access_omto_support_manager,
        OTK_HEAD_AGENT_SLUG: can_access_otk_head,
        QUALITY_ENGINEER_AGENT_SLUG: can_access_quality_engineer,
        QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG: can_access_quality_deputy_director,
        QUALITY_KPI_AGENT_SLUG: can_access_quality_kpi,
    }
    checker = checkers.get(agent_id)
    if checker is None:
        return False
    return await checker(db, user)


async def _require_role_workspace(
    db: DbSession,
    user: User,
    agent_id: str,
) -> None:
    if agent_id not in _ROLE_WORKSPACE_FORBIDDEN:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ролевой агент не найден")
    if not await _role_access(db, user, agent_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_ROLE_WORKSPACE_FORBIDDEN[agent_id],
        )


def _filter_dashboard_for_quality(
    payload: dict[str, Any],
    agent_id: str,
) -> dict[str, Any]:
    """Keep cases relevant to quality role workspaces."""
    groups = []
    for group in payload.get("groups") or []:
        cases = []
        for item in group.get("cases") or []:
            status_value = str(item.get("status") or "")
            assigned = item.get("assigned_agents") or []
            if agent_id in assigned or status_value in _QUALITY_CASE_STATUSES:
                cases.append(item)
        if cases:
            groups.append({**group, "cases": cases, "count": len(cases)})
    result = dict(payload)
    result["groups"] = groups
    return result


def _slim_quality_case(payload: dict[str, Any], agent_id: str) -> dict[str, Any]:
    metadata = payload.get("case_metadata") or {}
    output_key = f"{agent_id}_output"
    payload["case_metadata"] = {
        output_key: metadata.get(output_key),
        "quality_calculated_at": metadata.get("quality_calculated_at"),
        "quality_stage": metadata.get("quality_stage"),
        "next_quality_agent": metadata.get("next_quality_agent"),
        "omto_support_manager_output": metadata.get("omto_support_manager_output"),
    }
    payload["assigned_agents"] = [agent_id]
    payload["route_stages"] = []
    payload["events"] = [
        event
        for event in payload.get("events") or []
        if event.get("agent_id") == agent_id
        or event.get("event_type")
        in {
            "case_created_from_source",
            "source_document_changed",
            "case_archived_from_source",
            "role_agent_result_received",
            "role_agent_task_enqueued",
        }
    ]
    payload["timeline"] = [
        item
        for item in payload.get("timeline") or []
        if item.get("actor_id") == agent_id
        or item.get("kind")
        in {
            "case_created_from_source",
            "source_document_changed",
            "case_archived_from_source",
        }
    ]
    return payload


@router.get("/me/permissions", response_model=ProcurementPermissionsRead)
async def get_procurement_permissions(
    db: DbSession,
    current_user: CurrentUser,
) -> ProcurementPermissionsRead:
    can_access = await can_access_procurement_orchestrator(db, current_user)
    accessible: list[str] = []
    checks = [
        (PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG, can_access_production_preparation_engineer),
        (OMTO_SUPPORT_MANAGER_AGENT_SLUG, can_access_omto_support_manager),
        (OTK_HEAD_AGENT_SLUG, can_access_otk_head),
        (QUALITY_ENGINEER_AGENT_SLUG, can_access_quality_engineer),
        (QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG, can_access_quality_deputy_director),
        (QUALITY_KPI_AGENT_SLUG, can_access_quality_kpi),
    ]
    for slug, checker in checks:
        if await checker(db, current_user):
            accessible.append(slug)
    return ProcurementPermissionsRead(
        can_access_orchestrator=can_access,
        can_access_role_workspace=bool(accessible),
        accessible_role_agents=accessible,
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
    await _require_role_workspace(db, current_user, agent_id)
    if agent_id == QUALITY_KPI_AGENT_SLUG:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Для KPI используйте /procurement/quality-kpi/dashboard",
        )
    source_type = (
        "production_material_order"
        if agent_id == PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG
        else None
    )
    payload = await ProcurementOrchestratorService(db, enqueue_case=False).list_dashboard(
        view=view,
        source_type=source_type,
    )
    if agent_id in {
        OTK_HEAD_AGENT_SLUG,
        QUALITY_ENGINEER_AGENT_SLUG,
        QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG,
    }:
        payload = _filter_dashboard_for_quality(payload, agent_id)
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
    await _require_role_workspace(db, current_user, agent_id)
    payload = await ProcurementOrchestratorService(db, enqueue_case=False).get_case(case_id)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кейс не найден")

    if agent_id == PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG:
        if payload.get("source_type") != "production_material_order":
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
    elif agent_id == OMTO_SUPPORT_MANAGER_AGENT_SLUG:
        metadata = payload.get("case_metadata") or {}
        payload["case_metadata"] = {
            "omto_support_manager_output": metadata.get("omto_support_manager_output"),
            "omto_calculated_at": metadata.get("omto_calculated_at"),
        }
        payload["assigned_agents"] = [OMTO_SUPPORT_MANAGER_AGENT_SLUG]
        payload["route_stages"] = []
        payload["events"] = [
            event
            for event in payload.get("events") or []
            if event.get("agent_id") == OMTO_SUPPORT_MANAGER_AGENT_SLUG
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
            if item.get("actor_id") == OMTO_SUPPORT_MANAGER_AGENT_SLUG
            or item.get("kind")
            in {
                "case_created_from_source",
                "source_document_changed",
                "case_archived_from_source",
            }
        ]
    else:
        payload = _slim_quality_case(payload, agent_id)
    return ProcurementCaseDetail.model_validate(payload)


@router.get("/quality-kpi/dashboard")
async def get_quality_kpi_dashboard(
    db: DbSession,
    current_user: CurrentUser,
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None),
) -> dict[str, Any]:
    if not await can_access_quality_kpi(db, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_ROLE_WORKSPACE_FORBIDDEN[QUALITY_KPI_AGENT_SLUG],
        )
    orch = ProcurementOrchestratorService(db, enqueue_case=False)
    dashboard = await orch.list_dashboard(view="processing")
    events: list[dict[str, Any]] = []
    quality_cases: list[dict[str, Any]] = []
    for group in dashboard.get("groups") or []:
        for case in group.get("cases") or []:
            status_value = str(case.get("status") or "")
            if status_value in _QUALITY_CASE_STATUSES or case.get("assigned_agents"):
                meta = case.get("metadata") if isinstance(case.get("metadata"), dict) else {}
                quality_meta = meta.get("quality_kpi") if isinstance(meta.get("quality_kpi"), dict) else {}
                # Не подставляем «успех» без фактических флагов KPI / СТО.
                quality_cases.append(
                    {
                        "incoming_control_sla_met": quality_meta.get("incoming_control_sla_met"),
                        "available_without_releasing_status": quality_meta.get(
                            "available_without_releasing_status"
                        ),
                        "control_traceability_ok": quality_meta.get("control_traceability_ok"),
                        "mandatory_data_ok": quality_meta.get("mandatory_data_ok"),
                        "purchase_without_basis": quality_meta.get("purchase_without_basis"),
                        "procurement_sla_met": quality_meta.get("procurement_sla_met"),
                        "receipt_sla_met": quality_meta.get("receipt_sla_met"),
                        "hx_action_by_ai": quality_meta.get("hx_action_by_ai"),
                    }
                )
            for event in case.get("events") or []:
                if isinstance(event, dict):
                    events.append(event)
            latest = case.get("latest_result")
            if isinstance(latest, dict):
                events.append(
                    {
                        "agent_id": latest.get("agent_id") or case.get("current_agent_id"),
                        "role_status": latest.get("role_status") or latest.get("status"),
                        "output_data": latest.get("output_data") or {},
                        "checked": True,
                    }
                )

    result = await QualityKpiService().run(
        {
            "case_id": "kpi-dashboard",
            "correlation_id": "kpi-dashboard",
            "source_data": {
                "period_from": period_from,
                "period_to": period_to,
                "agent_events": events,
                "quality_cases": quality_cases,
            },
            "role_context": {},
        },
        agent_id=QUALITY_KPI_AGENT_SLUG,
    )
    return result.output_data


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
