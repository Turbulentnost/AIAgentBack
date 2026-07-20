from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.common.schemas import AgentResult, BaseAgentInput


class FinanceUpstreamContext(BaseModel):
    """Upstream snapshot fields used for S10 / price / one-off checks."""

    supplier_id: str | None = None
    invoice_verified: bool | None = None
    price_match: bool | None = None
    price_deviation_pct: Decimal | None = None
    project_price_valid_until: date | None = None
    market_quotes_count: int | None = None
    supplier_quotes: list[dict[str, Any]] = Field(default_factory=list)
    contract_status: str | None = None
    sz_required: bool | None = None


class FinanceCaseContext(BaseModel):
    """Minimal case snapshot for finance director (contour 4)."""

    model_config = ConfigDict(populate_by_name=True)

    amount: Decimal | None = None
    s10_week_remaining: Decimal | None = None
    # Приложение Б.5 / Г.3: синоним недельного лимита закупки (S10)
    procurement_limit_week_remaining: Decimal | None = None
    escalation_reason_code: str | None = None
    production_need_date: date | None = None
    payment_request_id: str | None = None
    cfo_code: str | None = None
    payment_date_status: Literal["project", "confirmed"] | None = None
    # Orchestrator case patches — used to break cfo↔finance next_roles loops
    cfo_approved: bool | None = None
    financial_decision: str | None = None
    upstream: FinanceUpstreamContext = Field(default_factory=FinanceUpstreamContext)

    @model_validator(mode="after")
    def _sync_s10_aliases(self) -> FinanceCaseContext:
        if self.s10_week_remaining is None and self.procurement_limit_week_remaining is not None:
            self.s10_week_remaining = self.procurement_limit_week_remaining
        elif self.procurement_limit_week_remaining is None and self.s10_week_remaining is not None:
            self.procurement_limit_week_remaining = self.s10_week_remaining
        return self


FinanceDirectorRoleStatus = Literal[
    "waiting_human",
    "completed",
    "failed",
    "data_check",
    "blocked",
    "escalated",
]


class FinanceDirectorAgentRequest(BaseAgentInput):
    case_id: str = Field(..., min_length=1, max_length=128)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    caller_agent_id: str = Field(default="contour4_orchestrator", max_length=128)
    trigger: str = Field(default="", max_length=256)
    case_context: FinanceCaseContext = Field(default_factory=FinanceCaseContext)
    payload: dict[str, Any] = Field(default_factory=dict)
    human_action: str | None = None
    human_payload: dict[str, Any] = Field(default_factory=dict)


class FinanceDirectorAgentResult(AgentResult):
    case_id: str
    correlation_id: str
    role_status: FinanceDirectorRoleStatus
    wait_reason: str | None = None
    suggested_action: str | None = None
    output_data: dict[str, Any] = Field(default_factory=dict)
    next_roles_suggested: list[str] = Field(default_factory=list)


__all__ = [
    "FinanceCaseContext",
    "FinanceDirectorAgentRequest",
    "FinanceDirectorAgentResult",
    "FinanceDirectorRoleStatus",
    "FinanceUpstreamContext",
]
