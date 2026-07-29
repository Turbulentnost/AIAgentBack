from __future__ import annotations

from typing import Any, TypedDict


class ProcurementPipelineState(TypedDict, total=False):
    """Case context for the end-to-end AI procurement LangGraph chain."""

    case_id: str
    case_number: str
    correlation_id: str
    source_type: str
    positions: list[dict[str, Any]]
    case_context: dict[str, Any]

    # Routing / agents
    current_agent: str | None
    next_agent: str | None
    picker_agent: str | None

    # Coverage
    allocation: dict[str, Any] | None
    coverage_status: str  # covered | deficit | data_insufficient | failed
    deficit_positions: list[dict[str, Any]]

    # Purchase path
    evaluation: dict[str, Any] | None
    recommendation: dict[str, Any] | None
    purchase_order_draft: dict[str, Any] | None
    approval: dict[str, Any] | None

    # Quality handoff
    quality_stage: str | None

    # Control
    status: str
    stage: str
    case_status: str
    stop_reason: str | None
    requires_human: bool
    auto_approve: bool
    summary: str
    errors: list[str]
    kpi_flags: dict[str, Any]


__all__ = ["ProcurementPipelineState"]
