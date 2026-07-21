from __future__ import annotations

from typing import Any, TypedDict

from app.agents.common.state import BaseAgentState


class QualityControlState(BaseAgentState, total=False):
    case_id: str
    correlation_id: str
    source_data: dict[str, Any]
    role_context: dict[str, Any]
    quality_stage: str
    presentation: dict[str, Any]
    category: str
    present_docs: list[str]
    lot_qty: float | None
    scrap_pct: float | None
    analog_in_nomenclature: bool | None
    doc_findings: list[dict[str, Any]]
    sample_rule: dict[str, Any]
    scrap_decision: dict[str, Any]
    deadlines: dict[str, Any]
    mandatory_documents: list[dict[str, Any]]
    next_role: str | None
    next_status: str | None
    draft_artifacts: dict[str, Any]
    parallel_results: dict[str, Any]
    quality_control: dict[str, Any]
    actions: list[str]
    requires_human: bool


__all__ = ["QualityControlState"]
