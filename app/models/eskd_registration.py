from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import EskdDocumentKind, EskdRegistrationStatus


class EskdDocumentRegistration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Регистрация конструкторского документа для проверки по ЕСКД через агента НД."""

    __tablename__ = "eskd_document_registrations"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    qms_document_card_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_cards.id", ondelete="SET NULL"),
        index=True,
    )
    nd_control_department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nd_control_departments.id", ondelete="SET NULL"),
        index=True,
    )
    registered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    agent_slug: Mapped[str] = mapped_column(String(128), default="nd_control_agent", index=True)
    designation: Mapped[str | None] = mapped_column(String(128), index=True)
    document_kind: Mapped[EskdDocumentKind] = mapped_column(default=EskdDocumentKind.OTHER, index=True)
    status: Mapped[EskdRegistrationStatus] = mapped_column(
        default=EskdRegistrationStatus.REGISTERED,
        index=True,
    )
    owner_department: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    celery_task_id: Mapped[str | None] = mapped_column(String(128))

    document: Mapped["Document"] = relationship(foreign_keys=[document_id])
    qms_document_card: Mapped["QmsDocumentCard | None"] = relationship(foreign_keys=[qms_document_card_id])
