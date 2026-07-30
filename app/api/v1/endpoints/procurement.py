from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.agents.quality_kpi_agent.service import QualityKpiService
from app.agents.warehouse_picker_agent.department import is_montage_section_2_department
from app.api.deps import CurrentUser, DbSession
from app.models.enums import ProcurementCaseStatus
from app.models.user import User
from app.schemas.procurement import (
    ProcurementCaseDetail,
    ProcurementCaseEventRead,
    ProcurementDashboardRead,
    ProcurementEngineerActionRead,
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
    PRODUCTION_DISPATCHER_AGENT_SLUG,
    PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG,
    PURCHASE_MANAGER_AGENT_SLUG,
    QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG,
    QUALITY_ENGINEER_AGENT_SLUG,
    QUALITY_KPI_AGENT_SLUG,
    WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG,
    WAREHOUSE_PICKER_AGENT_SLUG,
    can_access_omto_support_manager,
    can_access_otk_head,
    can_access_procurement_orchestrator,
    can_access_production_dispatcher,
    can_access_production_preparation_engineer,
    can_access_purchase_manager,
    can_access_quality_deputy_director,
    can_access_quality_engineer,
    can_access_quality_kpi,
    can_access_warehouse_complex_chief,
    can_access_warehouse_picker,
    can_refresh_procurement_orchestrator,
)

router = APIRouter(prefix="/procurement", tags=["procurement"])

_ROLE_WORKSPACE_FORBIDDEN = {
    PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG: (
        "Рабочее место доступно только инженеру по подготовке производства"
    ),
    PRODUCTION_DISPATCHER_AGENT_SLUG: (
        "Рабочее место доступно только диспетчеру производства"
    ),
    WAREHOUSE_PICKER_AGENT_SLUG: (
        "Рабочее место доступно только кладовщику-комплектовщику"
    ),
    WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG: (
        "Рабочее место доступно только начальнику складского комплекса"
    ),
    PURCHASE_MANAGER_AGENT_SLUG: (
        "Рабочее место доступно только менеджеру по закупкам"
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
            detail="ИИ-агент по закупкам доступен только администратору системы",
        )


async def _role_access(db: DbSession, user: User, agent_id: str) -> bool:
    checkers = {
        PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG: can_access_production_preparation_engineer,
        PRODUCTION_DISPATCHER_AGENT_SLUG: can_access_production_dispatcher,
        WAREHOUSE_PICKER_AGENT_SLUG: can_access_warehouse_picker,
        WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG: can_access_warehouse_complex_chief,
        PURCHASE_MANAGER_AGENT_SLUG: can_access_purchase_manager,
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
) -> str:
    if agent_id not in _ROLE_WORKSPACE_FORBIDDEN:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ролевой агент не найден")
    if not await _role_access(db, user, agent_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_ROLE_WORKSPACE_FORBIDDEN[agent_id],
        )
    return agent_id


def _dispatch_pending(service: ProcurementOrchestratorService) -> None:
    if not service.pending_dispatches:
        return
    from app.workers.tasks import run_procurement_case_task

    for case_id, task_id in service.pending_dispatches:
        run_procurement_case_task.apply_async(
            args=[case_id, task_id],
            queue="agents",
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
        (PRODUCTION_DISPATCHER_AGENT_SLUG, can_access_production_dispatcher),
        (WAREHOUSE_PICKER_AGENT_SLUG, can_access_warehouse_picker),
        (WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG, can_access_warehouse_complex_chief),
        (PURCHASE_MANAGER_AGENT_SLUG, can_access_purchase_manager),
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
        can_submit_role_result=bool(accessible),
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
    service = ProcurementOrchestratorService(db, enqueue_case=False)
    if agent_id == PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG:
        payload = await service.list_dashboard(
            view=view,
            source_type="production_material_order",
            engineer_workspace=True,
        )
    elif agent_id == WAREHOUSE_PICKER_AGENT_SLUG:
        payload = await service.list_dashboard(
            view=view,
            source_type="production_material_order",
            picker_workspace=True,
        )
    elif agent_id == WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG:
        payload = await service.list_dashboard(
            view=view,
            source_type="production_material_order",
            complex_workspace=True,
        )
    elif agent_id == PURCHASE_MANAGER_AGENT_SLUG:
        payload = await service.list_dashboard(
            view=view,
            source_type="production_material_order",
            purchase_manager_workspace=True,
        )
    elif agent_id == PRODUCTION_DISPATCHER_AGENT_SLUG:
        payload = await service.list_dashboard(
            view=view,
            dispatcher_workspace=True,
        )
    else:
        source_type = (
            "production_material_order"
            if agent_id == PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG
            else None
        )
        payload = await service.list_dashboard(
            view=view,
            source_type=source_type,
        )
        if agent_id in {
            OTK_HEAD_AGENT_SLUG,
            QUALITY_ENGINEER_AGENT_SLUG,
            QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG,
            OMTO_SUPPORT_MANAGER_AGENT_SLUG,
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
    metadata = payload.get("case_metadata") or {}

    if agent_id == PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG:
        if payload.get("source_type") != "production_material_order":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кейс не найден")
        if not (
            metadata.get("engineer_invoked_at")
            or metadata.get("production_preparation_engineer_output")
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кейс не найден")
        payload["case_metadata"] = {
            "production_order_1c_ref": metadata.get("production_order_1c_ref"),
            "production_order_type": metadata.get("production_order_type"),
            "production_preparation_engineer_output": metadata.get(
                "production_preparation_engineer_output"
            ),
            "engineer_evidence_fingerprint": metadata.get("engineer_evidence_fingerprint"),
            "engineer_calculated_at": metadata.get("engineer_calculated_at"),
            "engineer_decision_kind": metadata.get("engineer_decision_kind"),
            "engineer_invoked_at": metadata.get("engineer_invoked_at"),
            "engineer_workspace_archived_at": metadata.get(
                "engineer_workspace_archived_at"
            ),
            "engineer_action_at": metadata.get("engineer_action_at"),
            "engineer_critical_acknowledged_at": metadata.get(
                "engineer_critical_acknowledged_at"
            ),
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
    elif agent_id == WAREHOUSE_PICKER_AGENT_SLUG:
        if payload.get("source_type") != "production_material_order":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кейс не найден")
        if not (
            metadata.get("picker_invoked_at")
            or metadata.get("warehouse_picker_output")
            or is_montage_section_2_department(payload.get("department_name"))
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кейс не найден")
        payload["case_metadata"] = {
            "warehouse_picker_output": metadata.get("warehouse_picker_output"),
            "picker_evidence_fingerprint": metadata.get("picker_evidence_fingerprint"),
            "picker_calculated_at": metadata.get("picker_calculated_at"),
            "picker_decision_kind": metadata.get("picker_decision_kind"),
            "picker_invoked_at": metadata.get("picker_invoked_at"),
            "picker_workspace_archived_at": metadata.get("picker_workspace_archived_at"),
            "picker_action_at": metadata.get("picker_action_at"),
            "picker_confirmed_action": metadata.get("picker_confirmed_action"),
            "picker_critical_acknowledged_at": metadata.get(
                "picker_critical_acknowledged_at"
            ),
            "production_order_1c_ref": metadata.get("production_order_1c_ref"),
            "supplier_order_coverage": metadata.get("supplier_order_coverage"),
        }
        payload["assigned_agents"] = [WAREHOUSE_PICKER_AGENT_SLUG]
        payload["route_stages"] = []
        payload["events"] = [
            event
            for event in payload.get("events") or []
            if event.get("agent_id") == WAREHOUSE_PICKER_AGENT_SLUG
            or event.get("event_type")
            in {
                "case_created_from_source",
                "source_document_changed",
                "case_archived_from_source",
                "picker_conclusion_confirmed",
                "picker_critical_acknowledged",
                "picker_handoff_to_omto_chief",
            }
        ]
        payload["timeline"] = [
            item
            for item in payload.get("timeline") or []
            if item.get("actor_id") == WAREHOUSE_PICKER_AGENT_SLUG
            or item.get("kind")
            in {
                "case_created_from_source",
                "source_document_changed",
                "case_archived_from_source",
                "picker_conclusion_confirmed",
                "picker_critical_acknowledged",
                "picker_handoff_to_omto_chief",
            }
        ]
    elif agent_id == WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG:
        if payload.get("source_type") != "production_material_order":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кейс не найден")
        if not (
            metadata.get("complex_invoked_at")
            or metadata.get("warehouse_complex_output")
            or (
                payload.get("department_name") is not None
                and not is_montage_section_2_department(payload.get("department_name"))
            )
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кейс не найден")
        if is_montage_section_2_department(payload.get("department_name")):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кейс не найден")
        # Shared picker UI panel reads warehouse_picker_output / picker_* keys.
        payload["case_metadata"] = {
            "warehouse_picker_output": metadata.get("warehouse_complex_output"),
            "warehouse_complex_output": metadata.get("warehouse_complex_output"),
            "picker_evidence_fingerprint": metadata.get("complex_evidence_fingerprint"),
            "picker_calculated_at": metadata.get("complex_calculated_at"),
            "picker_decision_kind": metadata.get("complex_decision_kind"),
            "picker_invoked_at": metadata.get("complex_invoked_at"),
            "picker_workspace_archived_at": metadata.get("complex_workspace_archived_at"),
            "picker_action_at": metadata.get("complex_action_at"),
            "picker_confirmed_action": metadata.get("complex_confirmed_action"),
            "picker_critical_acknowledged_at": metadata.get(
                "complex_critical_acknowledged_at"
            ),
            "complex_decision_kind": metadata.get("complex_decision_kind"),
            "complex_invoked_at": metadata.get("complex_invoked_at"),
            "production_order_1c_ref": metadata.get("production_order_1c_ref"),
            "supplier_order_coverage": metadata.get("supplier_order_coverage"),
        }
        payload["assigned_agents"] = [WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG]
        payload["route_stages"] = []
        payload["events"] = [
            event
            for event in payload.get("events") or []
            if event.get("agent_id") == WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG
            or event.get("event_type")
            in {
                "case_created_from_source",
                "source_document_changed",
                "case_archived_from_source",
                "complex_conclusion_confirmed",
                "complex_critical_acknowledged",
                "complex_handoff_to_omto_chief",
                "complex_migrated_from_engineer",
            }
        ]
        payload["timeline"] = [
            item
            for item in payload.get("timeline") or []
            if item.get("actor_id") == WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG
            or item.get("kind")
            in {
                "case_created_from_source",
                "source_document_changed",
                "case_archived_from_source",
                "complex_conclusion_confirmed",
                "complex_critical_acknowledged",
                "complex_handoff_to_omto_chief",
                "complex_migrated_from_engineer",
            }
        ]
    elif agent_id == PURCHASE_MANAGER_AGENT_SLUG:
        if payload.get("source_type") != "production_material_order" or not (
            metadata.get("purchase_manager_invoked_at")
            or metadata.get("purchase_manager_output")
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кейс не найден")
        payload["case_metadata"] = {
            "purchase_manager_invoked_at": metadata.get("purchase_manager_invoked_at"),
            "purchase_manager_workspace_status": metadata.get(
                "purchase_manager_workspace_status"
            ),
            "purchase_manager_workspace_archived_at": metadata.get(
                "purchase_manager_workspace_archived_at"
            ),
            "purchase_manager_output": metadata.get("purchase_manager_output"),
            "supplier_order_coverage": metadata.get("supplier_order_coverage"),
        }
        payload["assigned_agents"] = [PURCHASE_MANAGER_AGENT_SLUG]
        payload["route_stages"] = []
        payload["events"] = [
            event
            for event in payload.get("events") or []
            if event.get("event_type")
            in {
                "supplier_order_detected",
                "supplier_coverage_changed",
                "purchase_manager_assigned",
                "picker_auto_archived",
                "case_archived_from_source",
            }
        ]
        payload["timeline"] = [
            item
            for item in payload.get("timeline") or []
            if item.get("kind")
            in {
                "supplier_order_detected",
                "supplier_coverage_changed",
                "purchase_manager_assigned",
                "picker_auto_archived",
                "case_archived_from_source",
            }
        ]
    elif agent_id == OMTO_SUPPORT_MANAGER_AGENT_SLUG:
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
    elif agent_id == PRODUCTION_DISPATCHER_AGENT_SLUG:
        is_dispatcher_case = (
            payload.get("source_type") == "reorder_point"
            or metadata.get("dispatcher_invoked_at")
            or metadata.get("production_dispatcher_output")
            or metadata.get("engineer_handoff_agent_id") == PRODUCTION_DISPATCHER_AGENT_SLUG
        )
        if not is_dispatcher_case:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кейс не найден")
        payload["case_metadata"] = {
            "production_dispatcher_output": metadata.get("production_dispatcher_output"),
            "production_preparation_engineer_output": metadata.get(
                "production_preparation_engineer_output"
            ),
            "dispatcher_evidence_fingerprint": metadata.get(
                "dispatcher_evidence_fingerprint"
            ),
            "dispatcher_calculated_at": metadata.get("dispatcher_calculated_at"),
            "dispatcher_decision_kind": metadata.get("dispatcher_decision_kind"),
            "dispatcher_invoked_at": metadata.get("dispatcher_invoked_at"),
            "dispatcher_workspace_archived_at": metadata.get(
                "dispatcher_workspace_archived_at"
            ),
            "dispatcher_action_at": metadata.get("dispatcher_action_at"),
            "dispatcher_confirmed_method": metadata.get("dispatcher_confirmed_method"),
            "dispatcher_critical_acknowledged_at": metadata.get(
                "dispatcher_critical_acknowledged_at"
            ),
            "stock_growth_coefficient": metadata.get("stock_growth_coefficient"),
        }
        payload["assigned_agents"] = [PRODUCTION_DISPATCHER_AGENT_SLUG]
        payload["route_stages"] = []
        payload["events"] = [
            event
            for event in payload.get("events") or []
            if event.get("agent_id") == PRODUCTION_DISPATCHER_AGENT_SLUG
            or event.get("event_type")
            in {
                "case_created_from_source",
                "source_document_changed",
                "case_archived_from_source",
                "engineer_handoff_to_chief_dispatcher",
                "dispatcher_supply_confirmed",
                "dispatcher_critical_acknowledged",
            }
        ]
        payload["timeline"] = [
            item
            for item in payload.get("timeline") or []
            if item.get("actor_id") == PRODUCTION_DISPATCHER_AGENT_SLUG
            or item.get("kind")
            in {
                "case_created_from_source",
                "source_document_changed",
                "case_archived_from_source",
                "engineer_handoff_to_chief_dispatcher",
                "dispatcher_supply_confirmed",
                "dispatcher_critical_acknowledged",
            }
        ]
    else:
        payload = _slim_quality_case(payload, agent_id)
    return ProcurementCaseDetail.model_validate(payload)


@router.post(
    "/role-agents/{agent_id}/cases/{case_id}/confirm-purchase",
    response_model=ProcurementEngineerActionRead,
)
async def confirm_engineer_purchase(
    agent_id: str,
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> ProcurementEngineerActionRead:
    await _require_role_workspace(db, current_user, agent_id)
    if agent_id != PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Действие недоступно")
    service = ProcurementOrchestratorService(db, enqueue_case=False)
    result = await service.confirm_engineer_purchase(case_id, user_id=str(current_user.id))
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Кейс не ожидает подтверждения закупки",
        )
    await db.commit()
    _dispatch_pending(service)
    return ProcurementEngineerActionRead.model_validate(result)


@router.post(
    "/role-agents/{agent_id}/cases/{case_id}/acknowledge-critical",
    response_model=ProcurementEngineerActionRead,
)
async def acknowledge_role_critical(
    agent_id: str,
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> ProcurementEngineerActionRead:
    await _require_role_workspace(db, current_user, agent_id)
    service = ProcurementOrchestratorService(db, enqueue_case=False)
    if agent_id == PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG:
        result = await service.acknowledge_engineer_critical(
            case_id, user_id=str(current_user.id)
        )
    elif agent_id == WAREHOUSE_PICKER_AGENT_SLUG:
        result = await service.acknowledge_picker_critical(
            case_id, user_id=str(current_user.id)
        )
    elif agent_id == WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG:
        result = await service.acknowledge_complex_chief_critical(
            case_id, user_id=str(current_user.id)
        )
    else:
        result = await service.acknowledge_dispatcher_critical(
            case_id, user_id=str(current_user.id)
        )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Кейс не ожидает ознакомления с критической ошибкой",
        )
    await db.commit()
    return ProcurementEngineerActionRead.model_validate(result)


@router.post(
    "/role-agents/{agent_id}/cases/{case_id}/confirm-conclusion",
    response_model=ProcurementEngineerActionRead,
)
async def confirm_picker_conclusion(
    agent_id: str,
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    action: str | None = Query(default=None),
) -> ProcurementEngineerActionRead:
    await _require_role_workspace(db, current_user, agent_id)
    if agent_id not in {
        WAREHOUSE_PICKER_AGENT_SLUG,
        WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG,
    }:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Действие недоступно")
    service = ProcurementOrchestratorService(db, enqueue_case=False)
    if agent_id == WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG:
        result = await service.confirm_complex_chief_conclusion(
            case_id,
            user_id=str(current_user.id),
            action=action,
        )
    else:
        result = await service.confirm_picker_conclusion(
            case_id,
            user_id=str(current_user.id),
            action=action,
        )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Кейс не ожидает подтверждения заключения по кладовой",
        )
    await db.commit()
    _dispatch_pending(service)
    return ProcurementEngineerActionRead.model_validate(result)


@router.post(
    "/role-agents/{agent_id}/cases/{case_id}/confirm-supply",
    response_model=ProcurementEngineerActionRead,
)
async def confirm_dispatcher_supply(
    agent_id: str,
    case_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    method: str | None = Query(default=None),
) -> ProcurementEngineerActionRead:
    await _require_role_workspace(db, current_user, agent_id)
    if agent_id != PRODUCTION_DISPATCHER_AGENT_SLUG:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Действие недоступно")
    service = ProcurementOrchestratorService(db, enqueue_case=False)
    result = await service.confirm_dispatcher_supply(
        case_id,
        user_id=str(current_user.id),
        method=method,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Кейс не ожидает подтверждения способа обеспечения",
        )
    await db.commit()
    _dispatch_pending(service)
    return ProcurementEngineerActionRead.model_validate(result)


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

    from app.workers.tasks import (
        poll_procurement_reorder_points,
        sync_procurement_material_orders,
    )

    sources_result = sync_procurement_material_orders.apply_async(
        queue="procurement_poll"
    )
    reorder_result = poll_procurement_reorder_points.apply_async(queue="procurement_poll")
    return ProcurementRefreshResult(
        status="accepted",
        summary={
            "celery_task_id": sources_result.id,
            "reorder_celery_task_id": reorder_result.id,
        },
    )
