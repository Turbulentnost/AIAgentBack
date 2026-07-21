from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.quality_control_agent.schemas import DispositionCode, QualityFinding


class QualityDeputyDirectorOutput(BaseModel):
    disposition: DispositionCode | None = None
    disposition_label: str | None = None
    execution_conditions: list[str] = Field(default_factory=list)
    act_ref: str | None = None
    within_allowed_list: bool = True
    next_status: str
    next_agent: str | None = None
    findings: list[QualityFinding] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    draft_artifacts: dict[str, Any] = Field(default_factory=dict)
    summary: str
    calculated_at: datetime | None = None
    quality_control: dict[str, Any] = Field(default_factory=dict)


__all__ = ["QualityDeputyDirectorOutput"]
