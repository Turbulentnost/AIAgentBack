from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.agents.common.schemas import AgentResult, BaseAgentInput
from app.models.enums import ProcurementSourceType

ProcurementRoleAgentStatus = Literal[
    "waiting_human",
    "waiting_external",
    "completed",
    "failed",
]


class ProcurementRoleAgentRequest(BaseAgentInput):
    case_id: str = Field(..., min_length=1, max_length=128)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    source_type: ProcurementSourceType
    source_1c_ref: str = Field(..., min_length=1, max_length=512)
    source_number: str | None = Field(default=None, max_length=128)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    caller_agent_id: str = Field(default="procurement_orchestrator", max_length=128)
    source_data: dict[str, Any] = Field(default_factory=dict)
    role_context: dict[str, Any] = Field(default_factory=dict)


class ProcurementRoleAgentResult(AgentResult):
    case_id: str
    correlation_id: str
    role_status: ProcurementRoleAgentStatus
    wait_reason: str | None = None
    output_data: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ProcurementRoleAgentRequest",
    "ProcurementRoleAgentResult",
    "ProcurementRoleAgentStatus",
]
