"""Модель данных PostgreSQL (раздел 7 ТЗ): email_messages, email_attachments."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    Text,
    TIMESTAMP,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class EmailMessageRow(Base):
    """7.1 Таблица email_messages."""

    __tablename__ = "email_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[str] = mapped_column(Text, unique=True)
    received_at: Mapped[datetime] = mapped_column(TIMESTAMP)
    processed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    mailbox: Mapped[str] = mapped_column(Text)
    sender_email: Mapped[str] = mapped_column(Text)
    sender_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_spam: Mapped[bool] = mapped_column(Boolean, default=False)
    spam_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    spam_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    contractor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_new_contractor: Mapped[bool] = mapped_column(Boolean, default=False)

    department_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    department_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    dept_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    priority: Mapped[str | None] = mapped_column(Text, nullable=True)

    summary_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    erp_document_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    erp_task_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(Text, default="processing")
    human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    attachments_count: Mapped[int] = mapped_column(Integer, default=0)
    agent_version: Mapped[str | None] = mapped_column(Text, nullable=True)

    attachments: Mapped[list["EmailAttachmentRow"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class EmailAttachmentRow(Base):
    """7.2 Таблица email_attachments."""

    __tablename__ = "email_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_messages.id", ondelete="CASCADE")
    )
    filename: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)

    message: Mapped[EmailMessageRow] = relationship(back_populates="attachments")
