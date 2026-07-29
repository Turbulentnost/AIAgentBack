from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class KnowledgeBaseItemRead(BaseModel):
    key: str
    display_name: str
    designation: str | None = None
    checked: bool
    check_count: int = 0
    last_checked_at: datetime | None = None
    last_check_run_id: uuid.UUID | None = None
    total_errors: int | None = None
    total_warnings: int | None = None
    has_ai_check: bool = False
    has_marking: bool = False
    marking_document_id: uuid.UUID | None = None
    marked_pages_count: int = 0
    marking_errors_count: int = 0
    marking_warnings_count: int = 0
    marking_updated_at: datetime | None = None
    human_verified_at: datetime | None = None
    pages_count: int | None = None
    verifiers: list[str] = []
    verifiers_count: int = 0


class KnowledgeBaseListResponse(BaseModel):
    items: list[KnowledgeBaseItemRead]
    total: int
    page: int
    size: int
    checked_count: int = 0
    unchecked_count: int = 0


class KnowledgeBaseVerifyRequest(BaseModel):
    check_run_id: uuid.UUID | None = None
    marking_document_id: uuid.UUID | None = None


class KnowledgeBaseVerifyResponse(BaseModel):
    item: KnowledgeBaseItemRead


class KnowledgeBaseDeleteResponse(BaseModel):
    key: str
    display_name: str
    deleted_marking_documents: int
    deleted_check_runs: int
