from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import (
    KnowledgeBaseAccessType,
    KnowledgeBaseAgentAccessMode,
    KnowledgeBaseChunkQualityStatus,
    KnowledgeBaseGrantType,
    KnowledgeBaseIndexErrorType,
    KnowledgeBaseIndexJobStatus,
    KnowledgeBaseIndexJobType,
    KnowledgeBaseRuleStatus,
    KnowledgeBaseSourcePrecheckStatus,
    KnowledgeBaseSourceStatus,
    KnowledgeBaseStatus,
)
from app.schemas.common import ORMModel


class KnowledgeBaseAccessGrantInput(BaseModel):
    grantee_type: KnowledgeBaseGrantType
    grantee_id: uuid.UUID | None = None
    access_type: KnowledgeBaseAccessType
    include_child_departments: bool = False
    expires_at: datetime | None = None
    reason: str | None = None
    comment: str | None = None
    responsible_user_id: uuid.UUID | None = None


class KnowledgeBaseAccessExceptionInput(BaseModel):
    grantee_type: KnowledgeBaseGrantType
    grantee_id: uuid.UUID
    access_type: KnowledgeBaseAccessType
    is_deny: bool = True
    expires_at: datetime | None = None
    reason: str | None = None
    comment: str | None = None


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    department_id: uuid.UUID | None = None
    responsible_user_id: uuid.UUID | None = None
    topic: str | None = None
    process_slug: str | None = None
    embedding_model: str | None = None
    metadata: dict | None = None
    access_grants: list[KnowledgeBaseAccessGrantInput] = Field(default_factory=list)
    source_document_ids: list[uuid.UUID] = Field(default_factory=list)


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    department_id: uuid.UUID | None = None
    responsible_user_id: uuid.UUID | None = None
    topic: str | None = None
    process_slug: str | None = None
    status: KnowledgeBaseStatus | None = None
    metadata: dict | None = None


class KnowledgeBaseRead(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    department_id: uuid.UUID | None
    owner_user_id: uuid.UUID | None
    responsible_user_id: uuid.UUID | None
    topic: str | None
    process_slug: str | None
    status: KnowledgeBaseStatus
    embedding_model: str | None
    vector_store: str
    qdrant_collection: str
    last_indexed_at: datetime | None
    deleted_at: datetime | None = None
    deleted_by_user_id: uuid.UUID | None = None
    is_public: bool
    sources_count: int
    fragments_count: int
    storage_bytes: int
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseListItem(KnowledgeBaseRead):
    can_access: bool = False
    can_search: bool = False
    can_delete: bool = False
    can_confirm_review: bool = False
    indexing_active: bool = False


class KnowledgeBaseStats(BaseModel):
    # Базы, к которым у текущего пользователя есть доступ на чтение или поиск.
    total_bases: int
    # Нерешённые ошибки индексации по доступным базам.
    indexing_errors_count: int
    # Суммарный объём данных в доступных базах (байты).
    storage_bytes: int
    # Доступные базы со статусом «Готова» (успешно проиндексированы).
    successfully_indexed_bases: int


class KnowledgeBaseSourceCreate(BaseModel):
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None = None


class KnowledgeBaseSourceRead(ORMModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    added_by_user_id: uuid.UUID | None
    added_at: datetime
    processing_status: KnowledgeBaseSourceStatus
    last_indexed_at: datetime | None
    fragments_count: int
    file_size: int | None
    access_snapshot: dict | None
    precheck_status: KnowledgeBaseSourcePrecheckStatus = KnowledgeBaseSourcePrecheckStatus.PENDING
    precheck_notes: str | None = None
    checksum: str | None = None
    quality_status: KnowledgeBaseChunkQualityStatus = KnowledgeBaseChunkQualityStatus.UNKNOWN
    pages_count: int | None = None
    created_at: datetime
    updated_at: datetime
    document_title: str | None = None
    original_filename: str | None = None
    extension: str | None = None
    relative_path: str | None = None
    department_id: uuid.UUID | None = None
    linked_agents_count: int = 0


class KnowledgeBaseChunkRead(ORMModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    source_id: uuid.UUID
    document_chunk_id: uuid.UUID
    is_excluded_from_search: bool
    exclusion_reason: str | None
    indexed_at: datetime | None
    embedding_status: str
    quality_status: KnowledgeBaseChunkQualityStatus = KnowledgeBaseChunkQualityStatus.UNKNOWN
    clause_number: str | None = None
    fragment_type: str | None = None
    access_snapshot: dict | None = None
    text: str | None = None
    metadata: dict | None = None
    chunk_index: int | None = None
    document_id: uuid.UUID | None = None
    document_title: str | None = None
    page_number: int | None = None
    section_title: str | None = None


class KnowledgeBaseChunkExclude(BaseModel):
    is_excluded_from_search: bool = True
    exclusion_reason: str | None = None


class KnowledgeBaseRuleCreate(BaseModel):
    text: str
    source_document_id: uuid.UUID | None = None
    source_chunk_id: uuid.UUID | None = None
    scope: str | None = None
    condition: str | None = None
    agent_action: str | None = None
    priority: int = 100
    status: KnowledgeBaseRuleStatus = KnowledgeBaseRuleStatus.DRAFT
    responsible_user_id: uuid.UUID | None = None


class KnowledgeBaseRuleRead(KnowledgeBaseRuleCreate, ORMModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseAccessGrantRead(KnowledgeBaseAccessGrantInput, ORMModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    granted_by_user_id: uuid.UUID | None
    granted_at: datetime
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseAccessExceptionRead(KnowledgeBaseAccessExceptionInput, ORMModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    granted_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseAccessUpdate(BaseModel):
    grants: list[KnowledgeBaseAccessGrantInput]
    exceptions: list[KnowledgeBaseAccessExceptionInput] = Field(default_factory=list)


class KnowledgeBaseAccessRead(BaseModel):
    grants: list[KnowledgeBaseAccessGrantRead]
    exceptions: list[KnowledgeBaseAccessExceptionRead]


class KnowledgeBaseAgentBindingInput(BaseModel):
    agent_id: uuid.UUID
    access_mode: KnowledgeBaseAgentAccessMode = KnowledgeBaseAgentAccessMode.SEARCH_ONLY
    expires_at: datetime | None = None
    is_enabled: bool = True


class KnowledgeBaseAgentBindingRead(KnowledgeBaseAgentBindingInput, ORMModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseIndexRequest(BaseModel):
    job_type: KnowledgeBaseIndexJobType = KnowledgeBaseIndexJobType.FULL
    source_id: uuid.UUID | None = None
    chunk_id: uuid.UUID | None = None


class KnowledgeBaseIndexCancelRequest(BaseModel):
    reason: str | None = None
    force: bool = False


class KnowledgeBaseIndexingJobRead(ORMModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    job_type: KnowledgeBaseIndexJobType
    status: KnowledgeBaseIndexJobStatus
    target_source_id: uuid.UUID | None
    target_chunk_id: uuid.UUID | None
    processed_sources_count: int
    created_fragments_count: int
    updated_fragments_count: int
    errors_count: int
    total_sources_count: int = 0
    total_chunks_count: int = 0
    extracted_sources_count: int = 0
    chunked_sources_count: int = 0
    embedded_chunks_count: int = 0
    qdrant_points_count: int = 0
    fulltext_chunks_count: int = 0
    processing_params: dict | None = None
    cancel_requested: bool = False
    cancel_requested_by_user_id: uuid.UUID | None = None
    cancel_requested_at: datetime | None = None
    cancel_reason: str | None = None
    duration_ms: int | None
    started_by_user_id: uuid.UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    embedding_model: str | None
    vector_store: str
    qdrant_collection: str | None
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseOverviewStats(BaseModel):
    sources_total: int = 0
    sources_processed: int = 0
    sources_with_errors: int = 0
    fragments_total: int = 0
    qdrant_points: int = 0
    fulltext_chunks: int = 0
    quality_percent: float = 0.0
    unresolved_errors: int = 0


class KnowledgeBaseIndexingErrorRead(ORMModel):
    id: uuid.UUID
    job_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    source_id: uuid.UUID | None
    error_type: KnowledgeBaseIndexErrorType
    technical_message: str | None
    user_message: str | None
    recommended_action: str | None
    is_resolved: bool
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseTestSearchRequest(BaseModel):
    query: str
    top_k: int = 5
    user_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None


class KnowledgeBaseSearchHit(BaseModel):
    content: str
    score: float
    accessible: bool
    access_reason: str
    knowledge_base_id: uuid.UUID
    knowledge_base_chunk_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    document_version_id: uuid.UUID | None = None
    chunk_id: uuid.UUID | None = None
    document_title: str | None = None
    page_number: int | None = None
    section_title: str | None = None
    clause_number: str | None = None
    metadata: dict | None = None


class KnowledgeBaseTestSearchResponse(BaseModel):
    hits: list[KnowledgeBaseSearchHit]
    answer_preview: str | None = None


class KnowledgeBaseSearchQueryCreate(BaseModel):
    query: str
    top_k: int = 5


class KnowledgeBaseSearchQueryRead(ORMModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    query: str
    top_k: int
    status: str
    answer: str | None = None
    hits: list[KnowledgeBaseSearchHit] | None = None
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
