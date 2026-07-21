from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.quality_control_agent.schemas import QualityFinding


class OtkHeadOutput(BaseModel):
    action: Literal[
        "assign_engineer",
        "confirm_nc_act",
        "annul_nc_act",
        "handoff_zdk",
        "await_presentation",
    ]
    assigned_engineer_id: str | None = None
    assigned_engineer_name: str | None = None
    sla_assign_wh: float = 2.0
    act_ref: str | None = None
    act_decision: Literal["confirm", "annul", "pending"] | None = None
    handoff_zdk_by: str | None = None
    next_status: str
    next_agent: str | None = None
    findings: list[QualityFinding] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    summary: str
    calculated_at: datetime | None = None
    quality_control: dict[str, Any] = Field(default_factory=dict)


__all__ = ["OtkHeadOutput"]
