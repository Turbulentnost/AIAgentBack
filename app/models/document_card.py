from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_cls]

from app.models.enums import (
    ConfidentialityLevel,
    DocumentCardStatus,
    QmsDocumentKind,
    QmsLevel,
)


class QmsDocumentCard(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """РљР°СЂС‚РѕС‡РєР° РґРѕРєСѓРјРµРЅС‚Р° РЎРњРљ (С‚Р°Р±Р»РёС†Р° document_cards). РќРµ РїСѓС‚Р°С‚СЊ СЃ nd_control_structural.DocumentCard."""
    __tablename__ = "document_cards"
    __table_args__ = (UniqueConstraint("document_id", name="uq_document_cards_document_id"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
    )
    document_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    document_name: Mapped[str] = mapped_column(String(512), index=True)
    document_type: Mapped[QmsDocumentKind] = mapped_column(Enum(QmsDocumentKind, name="qmsdocumentkind", values_callable=_enum_values), index=True)
    qms_level: Mapped[QmsLevel] = mapped_column(Enum(QmsLevel, name="qmslevel", values_callable=_enum_values), index=True)
    version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[DocumentCardStatus] = mapped_column(Enum(DocumentCardStatus, name="documentcardstatus", values_callable=_enum_values), default=DocumentCardStatus.DRAFT, index=True)
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
    original_storage_location: Mapped[str | None] = mapped_column(String(512))
    electronic_storage_location: Mapped[str | None] = mapped_column(String(512))
    has_process_diagram: Mapped[bool] = mapped_column(Boolean, default=False)
    has_acknowledgement_sheet: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledgement_targets: Mapped[list | None] = mapped_column(JSONB)
    confidentiality_level: Mapped[ConfidentialityLevel] = mapped_column(Enum(ConfidentialityLevel, name="confidentialitylevel", values_callable=_enum_values), default=ConfidentialityLevel.PUBLIC, index=True)
    change_history: Mapped[list | None] = mapped_column(JSONB)
    approval_history: Mapped[list | None] = mapped_column(JSONB)
    attachments: Mapped[list | None] = mapped_column(JSONB)
    archived_versions: Mapped[list | None] = mapped_column(JSONB)

    document: Mapped["Document"] = relationship(back_populates="qms_card")

