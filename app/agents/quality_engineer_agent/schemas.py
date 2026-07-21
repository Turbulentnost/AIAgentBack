from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.quality_control_agent.schemas import QualityFinding, QualitySampleRule


class QualityEngineerOutput(BaseModel):
    stage: Literal[
        "doc_check",
        "program",
        "inspection",
        "decision",
        "nc_act",
        "release",
    ]
    category: str
    mandatory_docs_ok: bool
    sample_rule: QualitySampleRule | None = None
    fitness_status: Literal["fit", "unfit", "doubtful", "pending"] = "pending"
    act_ref: str | None = None
    label_ref: str | None = None
    next_status: str
    next_agent: str | None = None
    findings: list[QualityFinding] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    draft_artifacts: dict[str, Any] = Field(default_factory=dict)
    summary: str
    calculated_at: datetime | None = None
    quality_control: dict[str, Any] = Field(default_factory=dict)


__all__ = ["QualityEngineerOutput"]
