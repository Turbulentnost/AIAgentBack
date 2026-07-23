from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class KpiMetric(BaseModel):
    id: str
    title: str
    formula: str
    value: float | None = None
    target: float | None = None
    target_label: str
    unit: str = "%"
    tone: Literal["ok", "warn", "bad", "unknown"] = "unknown"
    sample_size: int = 0
    details: dict[str, Any] = Field(default_factory=dict)


class AgentKpiBlock(BaseModel):
    agent_id: str
    agent_label: str
    common: list[KpiMetric] = Field(default_factory=list)
    special: list[KpiMetric] = Field(default_factory=list)
    below_target: list[str] = Field(default_factory=list)


class QualityKpiReport(BaseModel):
    period_from: str | None = None
    period_to: str | None = None
    agents: list[AgentKpiBlock] = Field(default_factory=list)
    system: list[KpiMetric] = Field(default_factory=list)
    summary: str
    calculated_at: datetime | None = None
    actions: list[str] = Field(default_factory=list)


__all__ = ["AgentKpiBlock", "KpiMetric", "QualityKpiReport"]
