from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import Field

from app.agents.common.schemas import AgentResult, BaseAgentInput, Finding


class TasksInput(BaseAgentInput):
    period_start: date | None = None
    period_end: date | None = None
    limit: int = Field(default=500, ge=1, le=1000)


class TasksStructuredResult(AgentResult):
    period_start: date | None = None
    period_end: date | None = None
    porucheniya: list[dict[str, Any]] = Field(default_factory=list)
    protocols: list[dict[str, Any]] = Field(default_factory=list)
    protocol_tasks: list[dict[str, Any]] = Field(default_factory=list)
    tasks_table: dict[str, Any] = Field(default_factory=dict)
    priority_summary: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    requires_user_review: bool = True


TasksResult = TasksStructuredResult
__all__ = [
    "TasksInput",
    "TasksResult",
    "TasksStructuredResult",
    "AgentResult",
    "Finding",
]
