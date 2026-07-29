from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class IntegrationDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_documents"

    external_document_id: Mapped[str] = mapped_column(String(256), index=True)
    source_system: Mapped[str] = mapped_column(String(64), index=True)
    designation: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    document_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sheet_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    author: Mapped[str | None] = mapped_column(String(256), nullable=True)
    department: Mapped[str | None] = mapped_column(String(256), nullable=True)
    product_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    files: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    related_documents: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    route_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "external_document_id",
            "revision",
            name="uq_integration_documents_source_ext_rev",
        ),
    )


class IntegrationJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_jobs"

    request_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("integration_documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    check_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("eskd_check_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_system: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="accepted")
    ruleset_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    critical_count: Mapped[int] = mapped_column(Integer, default=0)
    major_count: Mapped[int] = mapped_column(Integer, default=0)
    minor_count: Mapped[int] = mapped_column(Integer, default=0)
    blocks_workflow: Mapped[bool] = mapped_column(Boolean, default=False)
    result_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    submitted_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IntegrationExchangeLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "integration_exchange_log"

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default="now()",
        index=True,
    )
    sender: Mapped[str] = mapped_column(String(64), index=True)
    receiver: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("integration_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    external_document_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    designation: Mapped[str | None] = mapped_column(String(128), nullable=True)
    revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operation: Mapped[str] = mapped_column(String(64), index=True)
    result: Mapped[str] = mapped_column(String(32), index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_attempt: Mapped[int] = mapped_column(Integer, default=0)
    actor: Mapped[str | None] = mapped_column(String(256), nullable=True)
    payload_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class IntegrationWebhook(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_webhooks"

    name: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(String(2048))
    secret: Mapped[str | None] = mapped_column(String(256), nullable=True)
    events: Mapped[list] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source_system: Mapped[str | None] = mapped_column(String(64), nullable=True)


class IntegrationWebhookDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_webhook_deliveries"

    webhook_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_webhooks.id", ondelete="CASCADE"),
        index=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("integration_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IntegrationApiKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_api_keys"

    name: Mapped[str] = mapped_column(String(128))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    roles: Mapped[list] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
