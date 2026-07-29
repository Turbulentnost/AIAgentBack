from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class GostFinding(BaseModel):
    gost_key: str
    severity: Literal["ok", "warning", "error"] = "ok"
    pages: list[int] = Field(default_factory=list)
    note: str = ""


class PageLevelFinding(BaseModel):
    page: int
    gost_findings: list[GostFinding] = Field(default_factory=list)
    note: str = ""


class MarkingDocumentListItem(BaseModel):
    id: uuid.UUID
    designation: str | None
    source_filename: str
    pages_count: int
    created_at: datetime
    latest_label_id: uuid.UUID | None = None
    marked_pages_count: int = 0
    label_updated_at: datetime | None = None


class MarkingDocumentListResponse(BaseModel):
    items: list[MarkingDocumentListItem]
    total: int


class MarkingLabelCreate(BaseModel):
    document_id: uuid.UUID
    check_run_id: uuid.UUID | None = None
    is_rework: bool = False
    document_level: list[GostFinding] = Field(default_factory=list)
    page_level: list[PageLevelFinding] = Field(default_factory=list)
    problem_report: str = ""


class MarkingLabelUpdate(BaseModel):
    document_level: list[GostFinding] = Field(default_factory=list)
    page_level: list[PageLevelFinding] = Field(default_factory=list)
    problem_report: str = ""


class MarkingLabelRead(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    check_run_id: uuid.UUID | None
    is_rework: bool
    document_level: list[GostFinding]
    page_level: list[PageLevelFinding]
    problem_report: str | None
    created_at: datetime


class MarkingDocumentPage(BaseModel):
    page: int
    preview_url: str
    width: int | None = None
    height: int | None = None


class MarkingDocumentRead(BaseModel):
    id: uuid.UUID
    designation: str | None
    source_filename: str
    pages: list[MarkingDocumentPage]
    created_at: datetime
    reused_existing: bool = False
    has_saved_label: bool = False


class MarkingDocumentLookupResponse(BaseModel):
    found: bool
    document: MarkingDocumentRead | None = None
    marked_pages_count: int = 0
    label_updated_at: datetime | None = None


class MarkingLabelListResponse(BaseModel):
    items: list[MarkingLabelRead]
    total: int


class MarkingLabelSuggestedResponse(BaseModel):
    found: bool = False
    source: Literal["saved", "check_run", "none"] = "none"
    label_id: uuid.UUID | None = None
    check_run_id: uuid.UUID | None = None
    page_level: list[PageLevelFinding] = Field(default_factory=list)
    problem_report: str = ""


class GostStatItem(BaseModel):
    gost_key: str
    title: str
    error_count: int
    warning_count: int
    total: int
    after_ai_error_count: int = 0
    after_ai_warning_count: int = 0
    after_ai_total: int = 0


class GostStatsResponse(BaseModel):
    items: list[GostStatItem]
