from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProcurementPermissionsRead(BaseModel):
    can_access_orchestrator: bool
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


class ProcurementCaseSummary(BaseModel):
    id: str
    correlation_id: str
    source_type: str
    source_1c_ref: str
    source_number: str | None = None
    source_date: datetime | None = None
    source_status: str | None = None
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
    latest_result: dict[str, Any] | None = None
    case_metadata: dict[str, Any] | None = None
    positions: list[ProcurementCasePositionRead] = Field(default_factory=list)
    events: list[ProcurementCaseEventRead] = Field(default_factory=list)


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


class ProcurementDashboardRead(BaseModel):
    generated_at: datetime | str
    groups: list[ProcurementSourceGroupRead] = Field(default_factory=list)
    total_cases: int = 0


class ProcurementRefreshResult(BaseModel):
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ProcurementCaseDetail",
    "ProcurementCaseEventRead",
    "ProcurementCasePositionRead",
    "ProcurementCaseSummary",
    "ProcurementDashboardRead",
    "ProcurementPermissionsRead",
    "ProcurementRefreshResult",
    "ProcurementSourceGroupRead",
    "ProcurementSyncStatusRead",
]
