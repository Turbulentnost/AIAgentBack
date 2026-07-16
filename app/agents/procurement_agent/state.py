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
    plan: dict[str, Any] | None
    evidence: list[dict[str, Any]]
    next_action: dict[str, Any] | None
    current_tool_call: dict[str, Any] | None
    current_observation: dict[str, Any] | None
    iteration: int
    identical_call_counts: dict[str, int]
    successful_call_hashes: dict[str, str]
    coverage_result: dict[str, Any] | None
    human_action: dict[str, Any] | None
    stop_reason: str | None
    runtime: Any


__all__ = ["ProcurementCaseState"]
