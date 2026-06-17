from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class KnowledgeBaseDocumentListItem(BaseModel):
    document_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    file_name: str | None = None
    title: str | None = None
    parse_status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class KnowledgeBaseDocumentMetadata(BaseModel):
    document_id: uuid.UUID
    knowledge_base_id: uuid.UUID | None = None
    file_name: str | None = None
    title: str | None = None
    parse_status: str | None = None
    size: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class KnowledgeBaseDocumentChunk(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    text: str
    page_number: int | None = None
    section: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeBaseDocumentTextResult(BaseModel):
    document_id: uuid.UUID
    text: str
    status: Literal["ok", "empty", "error"] = "ok"
    source: Literal["extracted_text", "chunks", "none"] = "none"
    message: str | None = None


class KnowledgeBaseSearchFragment(BaseModel):
    fragment_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    document_title: str | None = None
    text: str
    score: float
    page_number: int | None = None
    section: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeBaseSearchResult(BaseModel):
    knowledge_base_id: uuid.UUID
    query: str
    status: Literal["ok", "empty"] = "ok"
    message: str | None = None
    fragments: list[KnowledgeBaseSearchFragment] = Field(default_factory=list)
