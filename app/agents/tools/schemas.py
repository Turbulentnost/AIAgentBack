from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class ToolContext(BaseModel):
    db: AsyncSession
    user: User
    agent_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None

    model_config = {"arbitrary_types_allowed": True}


class ToolInvocation(BaseModel):
    tool_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    agent_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None


class EmptyToolInput(BaseModel):
    pass


class ToolDescriptor(BaseModel):
    name: str
    description: str
    agent_description: str
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    required_permissions: list[str] = Field(default_factory=list)
    implemented: bool = True


class FetchPageViaUserBrowserInput(BaseModel):
    url: str
    extract_mode: Literal["text", "html", "screenshot", "table"] = "text"
    reason: str = Field(..., min_length=3, max_length=1000)
    timeout_seconds: int = Field(default=30, ge=1, le=60)


class BrowserPageTable(BaseModel):
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class FetchPageViaUserBrowserOutput(BaseModel):
    status: str
    url: str
    title: str | None = None
    text: str | None = None
    html: str | None = None
    tables: list[BrowserPageTable] = Field(default_factory=list)
    screenshot_document_id: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ListAvailableKnowledgeBasesInput(BaseModel):
    query: str | None = None


class AvailableKnowledgeBaseItem(BaseModel):
    knowledge_base_id: uuid.UUID
    code: str | None = None
    name: str
    description: str | None = None
    topic: str | None = None
    document_count: int
    fragments_count: int
    access_scope: str | None = None
    last_indexed_at: datetime | None = None


class ListAvailableKnowledgeBasesOutput(BaseModel):
    items: list[AvailableKnowledgeBaseItem]


class SearchKnowledgeBaseInput(BaseModel):
    query: str = Field(..., min_length=1)
    knowledge_base_ids: list[uuid.UUID] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=50)
    score_threshold: float | None = Field(default=None, ge=0, le=1)


class KnowledgeBaseSearchResult(BaseModel):
    fragment_id: uuid.UUID
    document_id: uuid.UUID | None = None
    document_title: str | None = None
    section_title: str | None = None
    page_number: int | None = None
    text: str
    score: float
    source: str
    knowledge_base_id: uuid.UUID


class SearchKnowledgeBaseOutput(BaseModel):
    query: str
    results: list[KnowledgeBaseSearchResult]


class GetKnowledgeFragmentInput(BaseModel):
    fragment_id: uuid.UUID
    include_neighbors: bool = True
    neighbors_count: int = Field(default=1, ge=0, le=5)


class KnowledgeFragmentNeighbor(BaseModel):
    fragment_id: uuid.UUID
    text: str
    page_number: int | None = None
    section_title: str | None = None


class GetKnowledgeFragmentOutput(BaseModel):
    fragment_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    document_id: uuid.UUID | None = None
    document_title: str | None = None
    section_title: str | None = None
    page_number: int | None = None
    text: str
    neighbors: list[KnowledgeFragmentNeighbor] = Field(default_factory=list)
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)
