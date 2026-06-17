from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ConfidenceLevel,
    KnowledgeBaseSourceStatus,
    NdBuildStatus,
    NdExtractionStatus,
    NdGraphEntityType,
    NdRelationExtractionType,
    NdRelationType,
    NdStructuralDocumentStatus,
    NdStructuralDocumentType,
)


class DocumentCard(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Структурная карточка нормативного документа (результат анализа nd_control_agent)."""

    __tablename__ = "nd_structural_document_cards"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_nd_structural_document_cards_document_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
    )
    file_name: Mapped[str | None] = mapped_column(String(512))
    document_code: Mapped[str | None] = mapped_column(String(128), index=True)
    title: Mapped[str | None] = mapped_column(String(512))
    document_type: Mapped[NdStructuralDocumentType | None] = mapped_column(index=True)
    version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[NdStructuralDocumentStatus] = mapped_column(
        default=NdStructuralDocumentStatus.DRAFT,
        index=True,
    )
    approval_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    purpose: Mapped[str | None] = mapped_column(Text)
    scope_text: Mapped[str | None] = mapped_column(Text)
    kb_parse_status: Mapped[KnowledgeBaseSourceStatus | None] = mapped_column(index=True)
    extraction_status: Mapped[NdExtractionStatus] = mapped_column(
        default=NdExtractionStatus.PENDING,
        index=True,
    )
    extraction_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    raw_extracted_json: Mapped[dict | None] = mapped_column(JSONB)

    document: Mapped["Document"] = relationship()
    knowledge_base: Mapped["KnowledgeBase"] = relationship()


class DepartmentProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Агрегированное описание отдела nd_control из прикреплённых баз знаний."""

    __tablename__ = "nd_department_profiles"
    __table_args__ = (
        UniqueConstraint("department_id", name="uq_nd_department_profiles_department_id"),
    )

    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nd_control_departments.id", ondelete="CASCADE"),
        index=True,
    )
    department_name: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str | None] = mapped_column(Text)
    functions_json: Mapped[list | None] = mapped_column(JSONB)
    source_knowledge_base_ids: Mapped[list | None] = mapped_column(JSONB)
    build_status: Mapped[NdBuildStatus] = mapped_column(
        default=NdBuildStatus.PENDING,
        index=True,
    )
    raw_profile_json: Mapped[dict | None] = mapped_column(JSONB)

    department: Mapped["NdControlDepartment"] = relationship()


class ProcessCard(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Карточка процесса, извлечённого из нормативных документов."""

    __tablename__ = "nd_process_cards"
    __table_args__ = (
        UniqueConstraint("canonical_name", name="uq_nd_process_cards_canonical_name"),
    )

    canonical_name: Mapped[str] = mapped_column(String(512), index=True)
    alternative_names: Mapped[list | None] = mapped_column(JSONB)
    description: Mapped[str | None] = mapped_column(Text)
    goal: Mapped[str | None] = mapped_column(Text)
    owner_candidate: Mapped[str | None] = mapped_column(String(255))
    owner_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    owner_confidence: Mapped[ConfidenceLevel | None] = mapped_column(index=True)
    source_document_ids: Mapped[list | None] = mapped_column(JSONB)
    inputs_json: Mapped[list | None] = mapped_column(JSONB)
    outputs_json: Mapped[list | None] = mapped_column(JSONB)
    actions_json: Mapped[list | None] = mapped_column(JSONB)
    roles_json: Mapped[list | None] = mapped_column(JSONB)
    forms_json: Mapped[list | None] = mapped_column(JSONB)
    systems_json: Mapped[list | None] = mapped_column(JSONB)
    resources_json: Mapped[list | None] = mapped_column(JSONB)


class NdRelation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Связь в графе нормативной документации."""

    __tablename__ = "nd_relations"
    __table_args__ = (
        Index("ix_nd_relations_source_type_source_id", "source_type", "source_id"),
        Index("ix_nd_relations_target_type_target_id", "target_type", "target_id"),
    )

    source_type: Mapped[NdGraphEntityType] = mapped_column(index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    source_name: Mapped[str] = mapped_column(String(512))
    relation_type: Mapped[NdRelationType] = mapped_column(index=True)
    target_type: Mapped[NdGraphEntityType] = mapped_column(index=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    target_name: Mapped[str] = mapped_column(String(512))
    confidence: Mapped[ConfidenceLevel] = mapped_column(default=ConfidenceLevel.MEDIUM, index=True)
    extraction_type: Mapped[NdRelationExtractionType] = mapped_column(index=True)
    evidence_json: Mapped[list | None] = mapped_column(JSONB)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


from app.models.document import Document  # noqa: E402
from app.models.knowledge_base import KnowledgeBase  # noqa: E402
from app.models.nd_control_registry import NdControlDepartment  # noqa: E402
