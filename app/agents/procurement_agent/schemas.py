from __future__ import annotations

from datetime import datetime
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
]
