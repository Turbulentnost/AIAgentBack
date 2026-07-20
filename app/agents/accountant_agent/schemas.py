from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.common.schemas import AgentResult, BaseAgentInput


class AccountantCaseContext(BaseModel):
    """Minimal case snapshot for accountant payment role (contour 4)."""

    payment_request_id: str | None = None
    fully_approved: bool | None = None
    payment_planned_date: date | None = None
    payment_status: str | None = None
    payment_actual_date: date | None = None
    amount: Decimal | None = None
    production_need_date: date | None = None
    cancel_requested: bool | None = None
    recalculated_delivery_date: date | None = None


AccountantRoleStatus = Literal[
    "waiting_human",
    "completed",
    "failed",
    "data_check",
    "blocked",
]


class AccountantAgentRequest(BaseAgentInput):
    case_id: str = Field(..., min_length=1, max_length=128)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    caller_agent_id: str = Field(default="contour4_orchestrator", max_length=128)
    trigger: str = Field(default="", max_length=256)
    case_context: AccountantCaseContext = Field(default_factory=AccountantCaseContext)
    payload: dict[str, Any] = Field(default_factory=dict)
    human_action: str | None = None
    human_payload: dict[str, Any] = Field(default_factory=dict)


class AccountantAgentResult(AgentResult):
    case_id: str
    correlation_id: str
    role_status: AccountantRoleStatus
    wait_reason: str | None = None
    suggested_action: str | None = None
    output_data: dict[str, Any] = Field(default_factory=dict)
    next_roles_suggested: list[str] = Field(default_factory=list)


__all__ = [
    "AccountantAgentRequest",
    "AccountantAgentResult",
    "AccountantCaseContext",
    "AccountantRoleStatus",
]
