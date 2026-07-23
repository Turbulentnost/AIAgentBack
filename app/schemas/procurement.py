from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ProcurementPermissionsRead(BaseModel):
    can_access_orchestrator: bool
    can_access_role_workspace: bool = False
    accessible_role_agents: list[str] = Field(default_factory=list)
    can_submit_role_result: bool = False
    can_refresh: bool
    is_superuser: bool


class ProcurementCasePositionRead(BaseModel):
    id: str
    line_id: str
    line_number: int
    nomenclature_id: str
    nomenclature_name: str | None = None
    characteristic_id: str | None = None
    unit: str | None = None
    quantity: str
    required_date: datetime | None = None
    supply_action: str | None = None
    cancelled: bool = False


class ProcurementCaseEventRead(BaseModel):
    id: str
    event_type: str
    agent_id: str | None = None
    actor_role: str | None = None
    previous_status: str | None = None
    new_status: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class ProcurementRouteStageRead(BaseModel):
    stage_id: str
    label: str
    order: int
    status: Literal["pending", "running", "completed", "blocked", "skipped"] = "pending"
    summary: str | None = None


class ProcurementTimelineEntryRead(BaseModel):
    id: str | None = None
    at: datetime | str | None = None
    kind: str
    title: str
    detail: str | None = None
    actor_id: str | None = None
    actor_label: str | None = None
    stage_id: str | None = None
    status: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ProcurementCurrentStateRead(BaseModel):
    status: str
    control_point: str | None = None
    current_agent_id: str | None = None
    current_agent_label: str | None = None
    requires_human_review: bool = False
    summary: str | None = None
    task_id: str | None = None
    task_status: str | None = None
    wait_status: str | None = None
    wait_reason: str | None = None
    closed_reason: str | None = None
    closed_reason_label: str | None = None
    source_active: bool = False


class ProcurementCaseSummary(BaseModel):
    id: str
    correlation_id: str
    source_type: str
    source_1c_ref: str
    source_number: str | None = None
    source_date: datetime | None = None
    source_status: str | None = None
    source_synced_at: datetime | None = None
    status: str
    control_point: str | None = None
    current_agent_id: str | None = None
    current_agent_name: str | None = None
    current_task_id: str | None = None
    required_date: datetime | None = None
    deadline_at: datetime | None = None
    positions_count: int = 0
    updated_at: datetime | None = None
    summary: str | None = None
    requires_human_review: bool = False
    closed_at: datetime | None = None
    closed_reason: str | None = None
    closed_reason_label: str | None = None
    reactivated_at: datetime | None = None
    source_active: bool = False
    engineer_bucket: Literal["success", "attention", "critical"] | None = None
    engineer_bucket_reason: str | None = None
    procurement_manager: dict[str, Any] = Field(default_factory=dict)
    suppliers: list[dict[str, Any]] = Field(default_factory=list)
    quotes: list[dict[str, Any]] = Field(default_factory=list)
    comparison: dict[str, Any] | None = None
    rfq_drafts: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    shipment_events: list[dict[str, Any]] = Field(default_factory=list)
    payment_document_draft: dict[str, Any] | None = None
    recommendation: dict[str, Any] | None = None
    recommendation_audit: list[dict[str, Any]] = Field(default_factory=list)
    # Bank allocation coverage for manager cards (warehouse/suppliers by deadline).
    # Must be declared so response_model validation does not strip these fields.
    order_coverage: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None


class ProcurementCaseDetail(ProcurementCaseSummary):
    source_entity_set: str | None = None
    source_database: str | None = None
    source_data_version: str | None = None
    initiator_1c_ref: str | None = None
    initiator_name: str | None = None
    department_1c_ref: str | None = None
    department_name: str | None = None
    warehouse_1c_ref: str | None = None
    warehouse_name: str | None = None
    warehouse_from_1c_ref: str | None = None
    warehouse_to_1c_ref: str | None = None
    organization_1c_ref: str | None = None
    priority_1c_ref: str | None = None
    assigned_agents: list[str] = Field(default_factory=list)
    deviation_summary: str | None = None
    need_title: str | None = None
    project_code: str | None = None
    project_name: str | None = None
    latest_result: dict[str, Any] | None = None
    case_metadata: dict[str, Any] | None = None
    positions: list[ProcurementCasePositionRead] = Field(default_factory=list)
    events: list[ProcurementCaseEventRead] = Field(default_factory=list)
    route_stages: list[ProcurementRouteStageRead] = Field(default_factory=list)
    timeline: list[ProcurementTimelineEntryRead] = Field(default_factory=list)
    current_state: ProcurementCurrentStateRead | None = None


class ProcurementSyncStatusRead(BaseModel):
    source_type: str
    label_ru: str
    entity_set: str | None = None
    available: bool
    unavailable_reason: str | None = None
    capability_status: str
    capability_message: str | None = None
    database_name: str | None = None
    last_polled_at: datetime | None = None
    last_success_at: datetime | None = None
    watermark_date: datetime | None = None
    last_error: str | None = None
    documents_seen: int = 0
    cases_created: int = 0
    cases_updated: int = 0
    cases_skipped: int = 0


class ProcurementSourceGroupRead(BaseModel):
    source_type: str
    label_ru: str
    entity_set: str | None = None
    available: bool
    unavailable_reason: str | None = None
    cases: list[ProcurementCaseSummary] = Field(default_factory=list)
    cases_count: int = 0
    sync: ProcurementSyncStatusRead


class ProcurementDashboardCounts(BaseModel):
    active: int = 0
    processing: int = 0
    archive: int = 0


class ProcurementDashboardRead(BaseModel):
    generated_at: datetime | str
    view: Literal["active", "processing", "archive"] = "active"
    groups: list[ProcurementSourceGroupRead] = Field(default_factory=list)
    total_cases: int = 0
    counts: ProcurementDashboardCounts = Field(default_factory=ProcurementDashboardCounts)
    material_allocation_summary: dict[str, Any] | None = None


class ProcurementRefreshResult(BaseModel):
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)


class ProcurementRoleAgentResumeRequest(BaseModel):
    role_status: Literal[
        "waiting_human",
        "waiting_external",
        "completed",
        "failed",
    ]
    summary: str | None = None
    wait_reason: str | None = None
    output_data: dict[str, Any] = Field(default_factory=dict)


class ProcurementRoleAgentResultRead(ProcurementRoleAgentResumeRequest):
    agent_id: str | None = None
    case_id: str
    correlation_id: str


__all__ = [
    "ProcurementCaseDetail",
    "ProcurementCaseEventRead",
    "ProcurementCasePositionRead",
    "ProcurementCaseSummary",
    "ProcurementCurrentStateRead",
    "ProcurementDashboardCounts",
    "ProcurementDashboardRead",
    "ProcurementPermissionsRead",
    "ProcurementRefreshResult",
    "ProcurementRoleAgentResumeRequest",
    "ProcurementRoleAgentResultRead",
    "ProcurementRouteStageRead",
    "ProcurementSourceGroupRead",
    "ProcurementSyncStatusRead",
    "ProcurementTimelineEntryRead",
]
