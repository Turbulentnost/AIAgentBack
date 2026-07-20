from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TasksTableColumnRead(BaseModel):
    key: str
    title: str


class TasksTableRead(BaseModel):
    columns: list[TasksTableColumnRead] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0


class TasksDashboardCountsRead(BaseModel):
    porucheniya_documents: int = 0
    porucheniya_tasks: int = 0
    protocol_documents: int = 0
    protocol_tasks: int = 0
    total_tasks: int = 0


class TasksMetricsRowRead(BaseModel):
    key: str
    title: str
    count: int = 0
    note: str | None = None


class TasksMetricsRead(BaseModel):
    rows: list[TasksMetricsRowRead] = Field(default_factory=list)
    report_day: str = ""


class TasksPermissionsRead(BaseModel):
    can_access_agent: bool


class TasksDashboardRead(BaseModel):
    author_fio: str
    manager_fio_source: str
    period_start: str
    period_end: str
    counts: TasksDashboardCountsRead
    priority_summary: dict[str, int] = Field(default_factory=dict)
    metrics: TasksMetricsRead
    tasks_table: TasksTableRead
    summary: str
    fetched_at: datetime
    error: str | None = None


class TasksDashboardRefreshRequest(BaseModel):
    period_start: str | None = None
    period_end: str | None = None
    limit: int = Field(default=500, ge=1, le=1000)
