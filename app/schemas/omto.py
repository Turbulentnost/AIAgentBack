from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OmtoPermissionsRead(BaseModel):
    accessible_role_agents: list[str] = Field(default_factory=list)
    is_superuser: bool = False


class OmtoAgentPassport(BaseModel):
    slug: str
    name: str
    name_full: str
    doc_ref: str
    registry_no: int
    position_role: str
    purpose: str
    contour: str
    autonomy: str


class OmtoKpiRow(BaseModel):
    id: str
    name: str
    target: str
    unit: str
    blocking: bool
    guardrail: bool
    source: str
    data_source: str  # "runs" | "onec"
    value: float | None
    status: str  # achieved | warn | below | no_data | pending_integration
    achieved: bool | None


class OmtoRuntimeStats(BaseModel):
    total_runs: int
    completed: int
    with_issues: int
    needs_input: int
    failed: int
    waiting_human: int
    hitl_required: int
    avg_latency_ms: int
    last_run_at: str | None


class OmtoKpiSummary(BaseModel):
    total: int
    achieved: int
    warn: int
    below: int
    pending: int
    blocking: int
    guardrail: int
    achievement_rate: float | None


class OmtoDashboardRead(BaseModel):
    agent: OmtoAgentPassport
    runtime: OmtoRuntimeStats
    kpi: list[OmtoKpiRow]
    summary: OmtoKpiSummary
    generated_at: str


class OmtoRunRequest(BaseModel):
    task_type: str
    correlation_id: str | None = None
    tenant_id: str = "default"
    task_payload: dict[str, Any] = Field(default_factory=dict)


class OmtoHitlPending(BaseModel):
    action: str | None = None
    approver_role: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    resume_node: str | None = None


class OmtoRunResultRead(BaseModel):
    agent_id: str
    status: str
    role_status: str
    summary: str | None
    data_confidence: str
    requires_human_review: bool
    correlation_id: str
    thread_id: str
    task_type: str
    wait_reason: str | None
    hitl_pending: OmtoHitlPending | None = None
    output_data: dict[str, Any]


class OmtoResumeRequest(BaseModel):
    thread_id: str
    resolution: str = "approved"  # approved | changes_requested | rejected
    passed: bool | None = None
    comment: str = ""


__all__ = [
    "OmtoAgentPassport",
    "OmtoDashboardRead",
    "OmtoHitlPending",
    "OmtoKpiRow",
    "OmtoKpiSummary",
    "OmtoPermissionsRead",
    "OmtoResumeRequest",
    "OmtoRunRequest",
    "OmtoRunResultRead",
    "OmtoRuntimeStats",
]
