from __future__ import annotations

from typing import Any

from app.agents.common.state import BaseAgentState


class ProcurementCaseState(BaseAgentState, total=False):
    correlation_id: str
    case_id: str | None
    source_type: str
    source_1c_ref: str
    caller_agent_id: str | None
    human_role: str
    autonomy_level: int
    requested_operation: str
    deadline: str | None
    idempotency_key: str
    source_data: dict[str, Any]
    case_status: str
    control_point: str | None
    action_class: str
    missing_fields: list[str]
    facts: list[dict[str, Any]]
    recommendation: str | None
    alternatives: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    rule_refs: list[str]
    artifacts: list[dict[str, Any]]
    required_approval: dict[str, Any] | None
    next_agent: str | None
    next_control_point: str | None
    warnings: list[str]


__all__ = ["ProcurementCaseState"]
