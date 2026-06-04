from __future__ import annotations

import uuid
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import DocumentType, SourceReliability

class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    title: Mapped[str] = mapped_column(String(512), index=True)
    doc_type: Mapped[DocumentType] = mapped_column(default=DocumentType.OTHER, index=True)
    storage_key: Mapped[str | None] = mapped_column(String(1024))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    doc_metadata: Mapped[dict | None] = mapped_column(JSONB)
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="document", cascade="all, delete-orphan")

class DocumentVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_versions"
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version_label: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[SourceReliability] = mapped_column(default=SourceReliability.NEEDS_CHECK)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    document: Mapped[Document] = relationship(back_populates="versions")
    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document_version", cascade="all, delete-orphan")

class DocumentChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_chunks"
    document_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    vector_id: Mapped[str | None] = mapped_column(String(128), index=True)
    chunk_metadata: Mapped[dict | None] = mapped_column(JSONB)
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
