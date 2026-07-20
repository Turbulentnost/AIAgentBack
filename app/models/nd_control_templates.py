from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import NdTemplateClassificationStatus, NdTemplateType


class NdControlTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "nd_control_templates"
    __table_args__ = (
        UniqueConstraint("template_type", name="uq_nd_control_templates_template_type"),
    )

    name: Mapped[str] = mapped_column(String(255), index=True)
    template_type: Mapped[NdTemplateType] = mapped_column(index=True)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )

    knowledge_base_links: Mapped[list["NdControlTemplateKnowledgeBase"]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
    )
    documents: Mapped[list["NdControlTemplateDocument"]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
    )


class NdControlTemplateKnowledgeBase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "nd_control_template_knowledge_bases"
    __table_args__ = (
        UniqueConstraint(
            "template_id",
            "knowledge_base_id",
            name="uq_nd_control_template_knowledge_bases_template_kb",
        ),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nd_control_templates.id", ondelete="CASCADE"),
        index=True,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
    )

    template: Mapped[NdControlTemplate] = relationship(back_populates="knowledge_base_links")
    knowledge_base: Mapped["KnowledgeBase"] = relationship()


class NdControlTemplateDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "nd_control_template_documents"
    __table_args__ = (
        UniqueConstraint(
            "template_id",
            "knowledge_base_source_id",
            name="uq_nd_control_template_documents_template_source",
        ),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nd_control_templates.id", ondelete="CASCADE"),
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
    detected_template_type: Mapped[NdTemplateType | None] = mapped_column(index=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    classification_status: Mapped[NdTemplateClassificationStatus] = mapped_column(
        default=NdTemplateClassificationStatus.PENDING,
        index=True,
    )
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    classified_by: Mapped[str | None] = mapped_column(String(32))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    template: Mapped[NdControlTemplate] = relationship(back_populates="documents")
    knowledge_base: Mapped["KnowledgeBase"] = relationship()
    knowledge_base_source: Mapped["KnowledgeBaseSource"] = relationship()
    document: Mapped["Document"] = relationship()


from app.models.document import Document  # noqa: E402
from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseSource  # noqa: E402
