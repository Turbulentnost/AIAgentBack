from __future__ import annotations

import uuid
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import DocumentProcessingStatus, DocumentType, SourceReliability, TextExtractStatus

class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    title: Mapped[str] = mapped_column(String(512), index=True)
    original_filename: Mapped[str | None] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(128))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    bucket_name: Mapped[str | None] = mapped_column(String(255))
    object_name: Mapped[str | None] = mapped_column(String(1024), index=True)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"),
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        index=True,
    )
    document_type: Mapped[DocumentType] = mapped_column(default=DocumentType.OTHER, index=True)
    processing_status: Mapped[DocumentProcessingStatus] = mapped_column(
        default=DocumentProcessingStatus.UPLOADED,
        index=True,
    )
    is_knowledge_base: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_indexed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    text_extract_status: Mapped[TextExtractStatus] = mapped_column(
        default=TextExtractStatus.NOT_STARTED,
        index=True,
    )
    extracted_text_object_name: Mapped[str | None] = mapped_column(String(1024))
    pages_count: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    # Legacy fields kept for compatibility with existing API code and old rows.
    doc_type: Mapped[DocumentType] = mapped_column(default=DocumentType.OTHER, index=True)
    storage_key: Mapped[str | None] = mapped_column(String(1024))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    doc_metadata: Mapped[dict | None] = mapped_column(JSONB)

    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")

class DocumentVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_versions"
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer, default=1, index=True)
    version_label: Mapped[str] = mapped_column(String(64))
    original_filename: Mapped[str | None] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(128))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    bucket_name: Mapped[str | None] = mapped_column(String(255))
    object_name: Mapped[str | None] = mapped_column(String(1024), index=True)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    processing_status: Mapped[DocumentProcessingStatus] = mapped_column(
        default=DocumentProcessingStatus.UPLOADED,
        index=True,
    )
    text_extract_status: Mapped[TextExtractStatus] = mapped_column(
        default=TextExtractStatus.NOT_STARTED,
        index=True,
    )
    extracted_text_object_name: Mapped[str | None] = mapped_column(String(1024))
    is_indexed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    qdrant_collection: Mapped[str | None] = mapped_column(String(255))
    qdrant_points_count: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(128), index=True)
    pages_count: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    # Legacy fields kept for compatibility with earlier version API.
    storage_key: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[SourceReliability] = mapped_column(default=SourceReliability.NEEDS_CHECK)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    document: Mapped[Document] = relationship(back_populates="versions")
    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document_version", cascade="all, delete-orphan")

class DocumentChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_chunks"
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer, index=True)
    section_title: Mapped[str | None] = mapped_column(String(512))
    token_count: Mapped[int | None] = mapped_column(Integer)
    qdrant_collection: Mapped[str | None] = mapped_column(String(255))
    qdrant_point_id: Mapped[str | None] = mapped_column(String(128), index=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255))
    is_indexed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    # Legacy fields kept for compatibility with earlier RAG code.
    content: Mapped[str] = mapped_column(Text)
    vector_id: Mapped[str | None] = mapped_column(String(128), index=True)
    chunk_metadata: Mapped[dict | None] = mapped_column(JSONB)
    document: Mapped[Document | None] = relationship(back_populates="chunks")
    document_version: Mapped[DocumentVersion] = relationship(back_populates="chunks")

class SourceReference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "source_references"
    task_result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("task_results.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    section: Mapped[str | None] = mapped_column(String(255))
    clause: Mapped[str | None] = mapped_column(String(255))
    external_url: Mapped[str | None] = mapped_column(String(1024))
    accessed_at: Mapped[str | None] = mapped_column(String(64))
    reliability: Mapped[SourceReliability] = mapped_column(default=SourceReliability.NEEDS_CHECK)
