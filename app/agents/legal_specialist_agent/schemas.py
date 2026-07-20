from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.common.schemas import AgentResult, BaseAgentInput


class LegalUpstreamContext(BaseModel):
    supplier_id: str | None = None
    contract_status: str | None = None
    supplier_inn: str | None = None


class LegalCaseContext(BaseModel):
    """Minimal case snapshot for legal specialist claims (contour 4)."""

    open_advances: list[dict[str, Any]] = Field(default_factory=list)
    # Zone 2: критические замечания по договору (ПЛ-34-048) — HITL юриста
    contract_critical_remarks: list[str] = Field(default_factory=list)
    escalation_reason_code: str | None = None
    upstream: LegalUpstreamContext = Field(default_factory=LegalUpstreamContext)


LegalSpecialistRoleStatus = Literal[
    "waiting_human",
    "completed",
    "failed",
    "data_check",
    "blocked",
]


class LegalSpecialistAgentRequest(BaseAgentInput):
    case_id: str = Field(..., min_length=1, max_length=128)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    caller_agent_id: str = Field(default="contour4_orchestrator", max_length=128)
    trigger: str = Field(default="", max_length=256)
    case_context: LegalCaseContext = Field(default_factory=LegalCaseContext)
    payload: dict[str, Any] = Field(default_factory=dict)
    human_action: str | None = None
    human_payload: dict[str, Any] = Field(default_factory=dict)


class LegalSpecialistAgentResult(AgentResult):
    case_id: str
    correlation_id: str
    role_status: LegalSpecialistRoleStatus
    wait_reason: str | None = None
    suggested_action: str | None = None
    output_data: dict[str, Any] = Field(default_factory=dict)
    next_roles_suggested: list[str] = Field(default_factory=list)


__all__ = [
    "LegalCaseContext",
    "LegalSpecialistAgentRequest",
    "LegalSpecialistAgentResult",
    "LegalSpecialistRoleStatus",
    "LegalUpstreamContext",
]
