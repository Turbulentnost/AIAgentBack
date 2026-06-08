from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import BrowserRunStatus
from app.schemas.common import ORMModel


ExtractMode = Literal["text", "html", "screenshot", "table"]


class BrowserRunCreate(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    extract_mode: ExtractMode = "text"
    reason: str = Field(..., min_length=3, max_length=1000)
    timeout_seconds: int = Field(default=30, ge=1, le=60)
    task_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None


class BrowserRunTable(BaseModel):
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class BrowserRunResult(BaseModel):
    status: BrowserRunStatus = BrowserRunStatus.COMPLETED
    title: str | None = None
    text: str | None = None
    html: str | None = None
    tables: list[BrowserRunTable] = Field(default_factory=list)
    screenshot_data_url: str | None = None
    error_message: str | None = None
    metadata: dict | None = None


class BrowserRunRead(ORMModel):
    id: uuid.UUID
    requested_by_agent_id: uuid.UUID | None
    requested_by_user_id: uuid.UUID
    task_id: uuid.UUID | None
    url: str
    method: str
    extract_mode: str
    status: BrowserRunStatus
    timeout_seconds: int
    title: str | None
    result_text: str | None
    result_html: str | None
    result_tables: list | None
    screenshot_object_name: str | None
    error_message: str | None
    finished_at: datetime | None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime
