from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.common.schemas import AgentResult, BaseAgentInput


class InvoiceRequisites(BaseModel):
    """Invoice requisites snapshot for chief accountant checks."""

    complete: bool | None = None
    inn: str | None = None
    kpp: str | None = None
    bank_account: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ApprovalsChain(BaseModel):
    cfo_approved: bool | None = None
    finance_approved: bool | None = None
    executive_approved: bool | None = None


class ChiefUpstreamContext(BaseModel):
    supplier_id: str | None = None


class ChiefCaseContext(BaseModel):
    """Minimal case snapshot for chief accountant (contour 4)."""

    registry_id: str | None = None
    payment_request_id: str | None = None
    invoice_requisites: InvoiceRequisites = Field(default_factory=InvoiceRequisites)
    approvals_chain: ApprovalsChain = Field(default_factory=ApprovalsChain)
    fully_approved: bool | None = None
    open_advances: list[dict[str, Any]] = Field(default_factory=list)
    upstream: ChiefUpstreamContext = Field(default_factory=ChiefUpstreamContext)


ChiefAccountantRoleStatus = Literal[
    "waiting_human",
    "completed",
    "failed",
    "data_check",
    "blocked",
]


class ChiefAccountantAgentRequest(BaseAgentInput):
    case_id: str = Field(..., min_length=1, max_length=128)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    caller_agent_id: str = Field(default="contour4_orchestrator", max_length=128)
    trigger: str = Field(default="", max_length=256)
    case_context: ChiefCaseContext = Field(default_factory=ChiefCaseContext)
    payload: dict[str, Any] = Field(default_factory=dict)
    human_action: str | None = None
    human_payload: dict[str, Any] = Field(default_factory=dict)


class ChiefAccountantAgentResult(AgentResult):
    case_id: str
    correlation_id: str
    role_status: ChiefAccountantRoleStatus
    wait_reason: str | None = None
    suggested_action: str | None = None
    output_data: dict[str, Any] = Field(default_factory=dict)
    next_roles_suggested: list[str] = Field(default_factory=list)


__all__ = [
    "ApprovalsChain",
    "ChiefAccountantAgentRequest",
    "ChiefAccountantAgentResult",
    "ChiefAccountantRoleStatus",
    "ChiefCaseContext",
    "ChiefUpstreamContext",
    "InvoiceRequisites",
]
