from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class GostSummaryRead(BaseModel):
    passed: list[str] = Field(default_factory=list)
    warnings: dict[str, list[int]] = Field(default_factory=dict)
    errors: dict[str, list[int]] = Field(default_factory=dict)


class CheckRunListItem(BaseModel):
    id: uuid.UUID
    job_id: str
    created_at: datetime
    original_filename: str | None
    designation: str | None
    status: str
    total_errors: int
    total_warnings: int
    pages_count: int | None
    version_no: int = 1
    created_by_login: str | None = None
    created_by_name: str | None = None
    verified_by_login: str | None = None
    verified_by_name: str | None = None
    human_verified_at: datetime | None = None
    gost_summary: GostSummaryRead | None = None
    progress_percent: float | None = None
    processed_pages: int | None = None


class CheckRunListResponse(BaseModel):
    items: list[CheckRunListItem]
    total: int
    page: int
    size: int


class CheckRunDetailRead(CheckRunListItem):
    content_type: str | None = None
    file_size_bytes: int | None = None
    file_sha256: str | None = None
    check_params: dict | None = None
    model: str | None = None
    adapter: str | None = None
    gost_summary: GostSummaryRead | None = None
    raw_result: dict | None = None
    document_key: str | None = None
    parent_run_id: uuid.UUID | None = None


class CheckRunChangeRead(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    parent_run_id: uuid.UUID | None = None
    version_no: int
    change_type: str
    summary: str
    changed_by_login: str | None = None
    changed_by_name: str | None = None
    created_at: datetime
    diff: dict | None = None


class CheckRunVersionRead(BaseModel):
    id: uuid.UUID
    version_no: int
    created_at: datetime
    created_by_login: str | None = None
    created_by_name: str | None = None
    total_errors: int
    total_warnings: int
    status: str
    human_verified_at: datetime | None = None
    verified_by_name: str | None = None
