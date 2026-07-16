from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.common.schemas import AgentResult, BaseAgentInput
from app.models.enums import ProcurementActionClass, ProcurementCaseStatus, ProcurementSourceType


class ProcurementAgentRequest(BaseAgentInput):
    correlation_id: str = Field(..., min_length=1, max_length=128)
    case_id: str | None = Field(default=None, max_length=128)
    source_type: ProcurementSourceType
    source_1c_ref: str = Field(..., min_length=1, max_length=512)
    caller_agent_id: str | None = Field(default=None, max_length=128)
    human_role: str = Field(..., min_length=1, max_length=255)
    autonomy_level: Literal[0, 1, 2] = 0
    requested_operation: str = Field(default="assess_need", min_length=1, max_length=128)
    deadline: datetime | None = None
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    source_data: dict[str, Any] = Field(default_factory=dict)


class ProcurementApprovalRequirement(BaseModel):
    action: str
    action_class: ProcurementActionClass
    approver_roles: list[str] = Field(default_factory=list)
    reason: str


class ProcurementArtifact(BaseModel):
    artifact_type: str
    status: str = "draft"
    reference: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ProcurementPlanStep(BaseModel):
    step_id: str
    objective: str
    status: Literal["pending", "running", "completed", "blocked", "skipped"] = "pending"
    allowed_tool_categories: list[str] = Field(default_factory=lambda: ["onec_read"])
    required_evidence: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    result_summary: str | None = None
    blocking_reason: str | None = None


class ProcurementPlan(BaseModel):
    plan_id: str
    case_id: str
    agent_id: str
    goal: str
    version: int = 1
    status: Literal["active", "completed", "blocked", "superseded"] = "active"
    steps: list[ProcurementPlanStep]
    dependencies: list[str] = Field(default_factory=list)
    expected_evidence: list[str] = Field(default_factory=list)
    completed_at: datetime | None = None
    replan_reason: str | None = None


class ProcurementEvidence(BaseModel):
    evidence_id: str
    source_system: str
    tool_name: str
    object_type: str
    object_id: str | None = None
    row_ids: list[str] = Field(default_factory=list)
    retrieved_at: datetime
    business_effective_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    freshness_status: Literal["fresh", "stale", "unknown"]
    correlation_id: str
    args_hash: str
    content_hash: str
    status: Literal["success", "capability_unavailable", "failed"] = "success"
    error_code: str | None = None
    error_message: str | None = None


class ProcurementNormalizedMCPRecord(BaseModel):
    source_system: str = "1C_ERP"
    source_tool: str
    source_object_type: str
    source_object_id: str
    nomenclature_id: str
    nomenclature_name: str
    characteristic_id: str | None = None
    warehouse_id: str | None = None
    organization_id: str | None = None
    quantity: Decimal
    unit: str | None = None
    status: str
    effective_at: datetime | None = None
    retrieved_at: datetime
    confirmation_status: str
    eligibility_status: Literal["eligible", "excluded", "data_insufficient"]
    exclusion_reason: str | None = None
    correlation_id: str


class ProcurementNeedPosition(BaseModel):
    line_id: str
    nomenclature_id: str | None = None
    nomenclature_name: str
    unit: str
    required_date: datetime | None = None
    gross_quantity: Decimal | None = Field(default=None, ge=0)
    product_quantity: Decimal | None = Field(default=None, ge=0)
    consumption_rate: Decimal | None = Field(default=None, ge=0)
    loss_factor: Decimal = Field(default=Decimal("1"), ge=0)
    calculation_source: Literal["direct_material_quantity", "production_norm"] | None = None
    match_status: Literal["exact", "ambiguous", "unmatched"] = "exact"
    possible_units: list[str] = Field(default_factory=list)


class ProcurementSupplyItem(BaseModel):
    supply_id: str
    source_type: Literal[
        "warehouse",
        "store_room",
        "semifinished",
        "in_transit",
        "supplier_order",
        "internal_transfer",
        "semifinished_production",
    ]
    nomenclature_id: str
    unit: str
    quantity: Decimal = Field(ge=0)
    confirmed: bool = True
    suitable: bool = True
    reserved_for_other: bool = False
    quarantine: bool = False
    defective: bool = False
    incoming_control_passed: bool = True
    expired: bool = False
    illiquid: bool = False
    exact_match: bool = True
    evidence_id: str


class ProcurementSupplyBreakdown(BaseModel):
    source_type: str
    quantity: Decimal
    supply_ids: list[str] = Field(default_factory=list)


class ProcurementExcludedSupply(BaseModel):
    supply_id: str
    source_type: str
    quantity: Decimal
    reason: str
    evidence_id: str


class ProcurementPositionCoverage(BaseModel):
    line_id: str
    nomenclature_id: str | None
    nomenclature_name: str
    unit: str
    required_date: datetime | None
    gross_requirement: Decimal
    gross_calculation_source: str
    available_supply: Decimal
    supply_breakdown: list[ProcurementSupplyBreakdown] = Field(default_factory=list)
    excluded_supply: list[ProcurementExcludedSupply] = Field(default_factory=list)
    net_requirement: Decimal
    status: Literal["covered", "partially_covered", "uncovered", "data_insufficient"]
    warnings: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ProcurementHumanActionCard(BaseModel):
    stopped_by: str
    obtained_data: list[str] = Field(default_factory=list)
    requested_from_human: list[str] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ProcurementKT1Result(BaseModel):
    case_id: str
    status: Literal["covered", "partially_covered", "uncovered", "data_insufficient"]
    source_basis: dict[str, Any]
    positions: list[ProcurementPositionCoverage]
    critical_positions: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommended_next_step: str
    human_action_required: ProcurementHumanActionCard | None = None
    completed_at: datetime


class ProcurementAgentResult(AgentResult):
    correlation_id: str
    case_id: str | None = None
    case_status: ProcurementCaseStatus
    control_point: str | None = None
    facts: list[dict[str, Any]] = Field(default_factory=list)
    recommendation: str | None = None
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    rule_refs: list[str] = Field(default_factory=list)
    artifacts: list[ProcurementArtifact] = Field(default_factory=list)
    required_approval: ProcurementApprovalRequirement | None = None
    next_agent: str | None = None
    next_control_point: str | None = None
    audit_event_id: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    plan: ProcurementPlan | None = None
    evidence: list[ProcurementEvidence] = Field(default_factory=list)
    coverage_result: ProcurementKT1Result | None = None
    human_action: ProcurementHumanActionCard | None = None


class ProcurementApprovalToken(BaseModel):
    token_id: str
    case_id: str
    action_hash: str
    approver_id: str
    approver_role: str
    decision: Literal["approved", "rejected"]
    scope: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime
    signature_ref: str | None = None


__all__ = [
    "ProcurementAgentRequest",
    "ProcurementAgentResult",
    "ProcurementApprovalRequirement",
    "ProcurementApprovalToken",
    "ProcurementArtifact",
    "ProcurementEvidence",
    "ProcurementHumanActionCard",
    "ProcurementKT1Result",
    "ProcurementNeedPosition",
    "ProcurementNormalizedMCPRecord",
    "ProcurementPlan",
    "ProcurementPlanStep",
    "ProcurementPositionCoverage",
    "ProcurementSupplyItem",
]
