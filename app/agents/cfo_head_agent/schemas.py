from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.common.schemas import AgentResult, BaseAgentInput


class PaymentMode(StrEnum):
    PREPAY = "prepay"
    POSTPAY = "postpay"
    STAGED = "staged"
    URGENT_PREPAY = "urgent_prepay"


class PaymentStage(BaseModel):
    stage_pct: Decimal
    planned_date: date | None = None
    description: str | None = None


class CfoCaseContext(BaseModel):
    """Minimal case snapshot for CFO head (contour 4). Embedded data preferred over live 1C."""

    payment_request_id: str | None = None
    payment_mode: PaymentMode | None = None
    payment_stages: list[PaymentStage] = Field(default_factory=list)
    amount: Decimal | None = None
    ds_limit: Decimal | None = None
    cfo_code: str | None = None
    expense_article: str | None = None
    project: str | None = None
    production_need_date: date | None = None
    delivery_days: int | None = None
    payment_planned_date: date | None = None
    supplier_id: str | None = None


CfoHeadRoleStatus = Literal[
    "waiting_human",
    "completed",
    "failed",
    "data_check",
    "blocked",
]


class CfoHeadAgentRequest(BaseAgentInput):
    case_id: str = Field(..., min_length=1, max_length=128)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    caller_agent_id: str = Field(default="contour4_orchestrator", max_length=128)
    case_context: CfoCaseContext = Field(default_factory=CfoCaseContext)
    payload: dict[str, Any] = Field(default_factory=dict)
    human_action: str | None = None
    human_payload: dict[str, Any] = Field(default_factory=dict)


class CfoHeadAgentResult(AgentResult):
    case_id: str
    correlation_id: str
    role_status: CfoHeadRoleStatus
    wait_reason: str | None = None
    suggested_action: str | None = None
    output_data: dict[str, Any] = Field(default_factory=dict)
    next_roles_suggested: list[str] = Field(default_factory=list)


__all__ = [
    "CfoCaseContext",
    "CfoHeadAgentRequest",
    "CfoHeadAgentResult",
    "CfoHeadRoleStatus",
    "PaymentMode",
    "PaymentStage",
]
