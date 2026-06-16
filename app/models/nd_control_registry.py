from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    NdConfidentialityLevel,
    NdDocumentCardStatus,
    NdDocumentType,
    NdQmsLevel,
)


class NdControlDepartment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "nd_control_departments"

    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )

    knowledge_base_links: Mapped[list["NdControlDepartmentKnowledgeBase"]] = relationship(
        back_populates="department",
        cascade="all, delete-orphan",
    )
    document_cards: Mapped[list["NdDocumentCard"]] = relationship(
        back_populates="department",
        cascade="all, delete-orphan",
    )


class NdControlDepartmentKnowledgeBase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "nd_control_department_knowledge_bases"
    __table_args__ = (
        UniqueConstraint(
            "department_id",
            "knowledge_base_id",
            name="uq_nd_control_department_knowledge_bases_department_kb",
        ),
    )

    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nd_control_departments.id", ondelete="CASCADE"),
        index=True,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
    )

    department: Mapped[NdControlDepartment] = relationship(back_populates="knowledge_base_links")
    knowledge_base: Mapped["KnowledgeBase"] = relationship()


class NdDocumentCard(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "nd_document_cards"
    __table_args__ = (
        UniqueConstraint("knowledge_base_source_id", name="uq_nd_document_cards_source_id"),
    )

    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nd_control_departments.id", ondelete="CASCADE"),
        index=True,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
    )
    knowledge_base_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_base_sources.id", ondelete="CASCADE"),
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        index=True,
    )

    document_code: Mapped[str | None] = mapped_column(String(128), index=True)
    document_name: Mapped[str | None] = mapped_column(String(512))
    document_type: Mapped[NdDocumentType | None] = mapped_column(index=True)
    qms_level: Mapped[NdQmsLevel | None] = mapped_column(index=True)
    version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[NdDocumentCardStatus] = mapped_column(
        default=NdDocumentCardStatus.DRAFT,
        index=True,
    )
    approval_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    process_owner: Mapped[str | None] = mapped_column(String(255))
    author: Mapped[str | None] = mapped_column(String(255))
    reviewer: Mapped[str | None] = mapped_column(String(255))
    approver: Mapped[str | None] = mapped_column(String(255))
    owner_department: Mapped[str | None] = mapped_column(String(255))
    scope: Mapped[str | None] = mapped_column(Text)
    related_processes: Mapped[list | None] = mapped_column(JSONB)
    related_departments: Mapped[list | None] = mapped_column(JSONB)
    related_documents: Mapped[list | None] = mapped_column(JSONB)
    normative_references: Mapped[list | None] = mapped_column(JSONB)
    record_forms: Mapped[list | None] = mapped_column(JSONB)
    retention_period: Mapped[str | None] = mapped_column(String(128))
    original_storage_location: Mapped[str | None] = mapped_column(String(255))
    electronic_storage_location: Mapped[str | None] = mapped_column(String(255))
    has_process_diagram: Mapped[bool] = mapped_column(Boolean, default=False)
    has_acknowledgement_sheet: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledgement_targets: Mapped[list | None] = mapped_column(JSONB)
    confidentiality_level: Mapped[NdConfidentialityLevel | None] = mapped_column(index=True)
    change_history: Mapped[list | None] = mapped_column(JSONB)
    approval_history: Mapped[list | None] = mapped_column(JSONB)
    attachments: Mapped[list | None] = mapped_column(JSONB)
    archived_versions: Mapped[list | None] = mapped_column(JSONB)

    department: Mapped[NdControlDepartment] = relationship(back_populates="document_cards")
    knowledge_base_source: Mapped["KnowledgeBaseSource"] = relationship()


from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseSource  # noqa: E402
