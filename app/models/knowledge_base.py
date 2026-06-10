from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
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


class KnowledgeBase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_bases"

    name: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"),
        index=True,
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    topic: Mapped[str | None] = mapped_column(String(255), index=True)
    process_slug: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[KnowledgeBaseStatus] = mapped_column(default=KnowledgeBaseStatus.DRAFT, index=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255))
    vector_store: Mapped[str] = mapped_column(String(64), default="qdrant", index=True)
    qdrant_collection: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    sources_count: Mapped[int] = mapped_column(Integer, default=0)
    fragments_count: Mapped[int] = mapped_column(Integer, default=0)
    storage_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    sources: Mapped[list["KnowledgeBaseSource"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )
    chunks: Mapped[list["KnowledgeBaseChunk"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )
    rules: Mapped[list["KnowledgeBaseRule"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )
    access_grants: Mapped[list["KnowledgeBaseAccessGrant"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )
    access_exceptions: Mapped[list["KnowledgeBaseAccessException"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )
    agent_bindings: Mapped[list["KnowledgeBaseAgentBinding"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )
    indexing_jobs: Mapped[list["KnowledgeBaseIndexingJob"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )


class KnowledgeBaseSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_base_sources"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_base_id",
            "document_version_id",
            name="uq_kb_sources_knowledge_base_id_document_version_id",
        ),
    )

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        index=True,
    )
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    processing_status: Mapped[KnowledgeBaseSourceStatus] = mapped_column(
        default=KnowledgeBaseSourceStatus.DRAFT,
        index=True,
    )
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    fragments_count: Mapped[int] = mapped_column(Integer, default=0)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    access_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    precheck_status: Mapped[KnowledgeBaseSourcePrecheckStatus] = mapped_column(
        default=KnowledgeBaseSourcePrecheckStatus.PENDING,
        index=True,
    )
    precheck_notes: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(128), index=True)
    quality_status: Mapped[KnowledgeBaseChunkQualityStatus] = mapped_column(
        default=KnowledgeBaseChunkQualityStatus.UNKNOWN,
        index=True,
    )
    pages_count: Mapped[int | None] = mapped_column(Integer)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="sources")
    chunks: Mapped[list["KnowledgeBaseChunk"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
    )


class KnowledgeBaseChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_base_chunks"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_base_id",
            "document_chunk_id",
            name="uq_kb_chunks_knowledge_base_id_document_chunk_id",
        ),
    )

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_base_sources.id", ondelete="CASCADE"),
        index=True,
    )
    document_chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"),
        index=True,
    )
    is_excluded_from_search: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    exclusion_reason: Mapped[str | None] = mapped_column(Text)
    excluded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    excluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    embedding_status: Mapped[str] = mapped_column(String(64), default="pending", index=True)
    clause_number: Mapped[str | None] = mapped_column(String(128))
    fragment_type: Mapped[str | None] = mapped_column(String(64), index=True)
    access_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    quality_status: Mapped[KnowledgeBaseChunkQualityStatus] = mapped_column(
        default=KnowledgeBaseChunkQualityStatus.UNKNOWN,
        index=True,
    )
    search_vector: Mapped[Any | None] = mapped_column(TSVECTOR)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="chunks")
    source: Mapped[KnowledgeBaseSource] = relationship(back_populates="chunks")


class KnowledgeBaseRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_base_rules"

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
    )
    text: Mapped[str] = mapped_column(Text)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_chunks.id", ondelete="SET NULL"))
    scope: Mapped[str | None] = mapped_column(String(255), index=True)
    condition: Mapped[str | None] = mapped_column(Text)
    agent_action: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    status: Mapped[KnowledgeBaseRuleStatus] = mapped_column(default=KnowledgeBaseRuleStatus.DRAFT, index=True)
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="rules")


class KnowledgeBaseAccessGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_base_access_grants"

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
    )
    grantee_type: Mapped[KnowledgeBaseGrantType] = mapped_column(index=True)
    grantee_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    access_type: Mapped[KnowledgeBaseAccessType] = mapped_column(index=True)
    include_child_departments: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="access_grants")


class KnowledgeBaseAccessException(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_base_access_exceptions"

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
    )
    grantee_type: Mapped[KnowledgeBaseGrantType] = mapped_column(index=True)
    grantee_id: Mapped[uuid.UUID] = mapped_column(index=True)
    access_type: Mapped[KnowledgeBaseAccessType] = mapped_column(index=True)
    is_deny: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="access_exceptions")


class KnowledgeBaseAgentBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_base_agent_bindings"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "agent_id", name="uq_kb_agent_bindings_knowledge_base_id_agent_id"),
    )

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    access_mode: Mapped[KnowledgeBaseAgentAccessMode] = mapped_column(
        default=KnowledgeBaseAgentAccessMode.SEARCH_ONLY,
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="agent_bindings")


class KnowledgeBaseIndexingJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_base_indexing_jobs"

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
    )
    job_type: Mapped[KnowledgeBaseIndexJobType] = mapped_column(index=True)
    status: Mapped[KnowledgeBaseIndexJobStatus] = mapped_column(
        default=KnowledgeBaseIndexJobStatus.QUEUED,
        index=True,
    )
    target_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_base_sources.id", ondelete="SET NULL"),
        index=True,
    )
    target_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_base_chunks.id", ondelete="SET NULL"),
        index=True,
    )
    processed_sources_count: Mapped[int] = mapped_column(Integer, default=0)
    created_fragments_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_fragments_count: Mapped[int] = mapped_column(Integer, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, default=0)
    total_sources_count: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks_count: Mapped[int] = mapped_column(Integer, default=0)
    extracted_sources_count: Mapped[int] = mapped_column(Integer, default=0)
    chunked_sources_count: Mapped[int] = mapped_column(Integer, default=0)
    embedded_chunks_count: Mapped[int] = mapped_column(Integer, default=0)
    qdrant_points_count: Mapped[int] = mapped_column(Integer, default=0)
    fulltext_chunks_count: Mapped[int] = mapped_column(Integer, default=0)
    processing_params: Mapped[dict | None] = mapped_column(JSONB)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    cancel_requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    started_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255))
    vector_store: Mapped[str] = mapped_column(String(64), default="qdrant")
    qdrant_collection: Mapped[str | None] = mapped_column(String(255), index=True)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="indexing_jobs")
    errors: Mapped[list["KnowledgeBaseIndexingError"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )


class KnowledgeBaseIndexingError(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_base_indexing_errors"

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_base_indexing_jobs.id", ondelete="CASCADE"),
        index=True,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_base_sources.id", ondelete="SET NULL"),
        index=True,
    )
    error_type: Mapped[KnowledgeBaseIndexErrorType] = mapped_column(index=True)
    technical_message: Mapped[str | None] = mapped_column(Text)
    user_message: Mapped[str | None] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    job: Mapped[KnowledgeBaseIndexingJob] = relationship(back_populates="errors")
