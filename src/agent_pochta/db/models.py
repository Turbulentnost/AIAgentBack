"""Модель данных PostgreSQL (раздел 7 ТЗ + staging справочников 1С)."""

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
    UniqueConstraint,
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

    contractor_id: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    raw_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_info_recipient: Mapped[bool] = mapped_column(Boolean, default=False)
    erp_retry_count: Mapped[int] = mapped_column(Integer, default=0)

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


class CatalogSyncRunRow(Base):
    """Журнал загрузок справочников (1С / JSON / будущие источники)."""

    __tablename__ = "catalog_sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="running")
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    contractors_count: Mapped[int] = mapped_column(Integer, default=0)
    departments_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ErpContractorRow(Base):
    """Staging контрагентов для RAG и поля «Партнёр» в 1С."""

    __tablename__ = "erp_contractors"
    __table_args__ = (UniqueConstraint("source", "contractor_id", name="uq_erp_contractors_source_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(Text, default="1c")
    contractor_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    emails_json: Mapped[str] = mapped_column(Text)
    department_codes_json: Mapped[str] = mapped_column(Text, default="[]")
    contractor_type: Mapped[str] = mapped_column(Text, default="клиент")
    external_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalog_sync_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP)


class DepartmentRow(Base):
    """Справочник отделов (код 1С, название, направление) для UI и маршрутизации."""

    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("code", name="uq_departments_code"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP)


class ChangeEventRow(Base):
    """Журнал изменений human-in-the-loop и связанных правок маршрута."""

    __tablename__ = "change_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, index=True)
    message_id: Mapped[str] = mapped_column(Text, index=True)
    email_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_messages.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(Text, index=True)
    field: Mapped[str] = mapped_column(Text)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(Text, default="operator")
    source: Mapped[str] = mapped_column(Text, default="system")


class ClassificationEventRow(Base):
    """Накопление смен отдела и спам-статуса (агент + оператор) для графиков и точности."""

    __tablename__ = "classification_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, index=True)
    message_id: Mapped[str] = mapped_column(Text, index=True)
    email_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(Text, index=True)
    event_type: Mapped[str] = mapped_column(Text, index=True)
    old_department_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_department_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_department_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_department_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_is_spam: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    new_is_spam: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    actor: Mapped[str] = mapped_column(Text, default="agent", index=True)
    source: Mapped[str] = mapped_column(Text, default="system")


class ErpDepartmentRow(Base):
    """Staging отделов для RAG и маршрутизации задач в 1С."""

    __tablename__ = "erp_departments"
    __table_args__ = (UniqueConstraint("source", "department_id", name="uq_erp_departments_source_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(Text, default="1c")
    department_id: Mapped[str] = mapped_column(Text)
    department_name: Mapped[str] = mapped_column(Text)
    head_name: Mapped[str] = mapped_column(Text, default="")
    responsibility: Mapped[str] = mapped_column(Text, default="")
    keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    external_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalog_sync_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP)
