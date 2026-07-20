from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.common.schemas import AgentResult, BaseAgentInput


class RegistryLine(BaseModel):
    """One payment registry line for executive director review."""

    payment_request_id: str | None = None
    cfo_approved: bool | None = None
    urgency: str | None = None
    amount: str | None = None
    cfo_code: str | None = None


class ExecutiveCaseContext(BaseModel):
    """Minimal case snapshot for executive director (contour 4)."""

    registry_id: str | None = None
    registry_lines: list[RegistryLine] = Field(default_factory=list)
    production_need_date: date | None = None


ExecutiveDirectorRoleStatus = Literal[
    "waiting_human",
    "completed",
    "failed",
    "data_check",
    "blocked",
]


class ExecutiveDirectorAgentRequest(BaseAgentInput):
    case_id: str = Field(..., min_length=1, max_length=128)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    caller_agent_id: str = Field(default="contour4_orchestrator", max_length=128)
    trigger: str = Field(default="", max_length=256)
    case_context: ExecutiveCaseContext = Field(default_factory=ExecutiveCaseContext)
    payload: dict[str, Any] = Field(default_factory=dict)
    human_action: str | None = None
    human_payload: dict[str, Any] = Field(default_factory=dict)


class ExecutiveDirectorAgentResult(AgentResult):
    case_id: str
    correlation_id: str
    role_status: ExecutiveDirectorRoleStatus
    wait_reason: str | None = None
    suggested_action: str | None = None
    output_data: dict[str, Any] = Field(default_factory=dict)
    next_roles_suggested: list[str] = Field(default_factory=list)


__all__ = [
    "ExecutiveCaseContext",
    "ExecutiveDirectorAgentRequest",
    "ExecutiveDirectorAgentResult",
    "ExecutiveDirectorRoleStatus",
    "RegistryLine",
]
