from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AveonShipmentScheduleVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "aveon_shipment_schedule_versions"
    __table_args__ = (
        UniqueConstraint(
            "country_scope",
            "file_sha256",
            name="uq_aveon_shipment_schedule_versions_country_hash",
        ),
    )

    country_scope: Mapped[str] = mapped_column(String(64), default="russia", index=True)
    source_type: Mapped[str] = mapped_column(String(64), default="admin_upload", index=True)
    file_name: Mapped[str] = mapped_column(String(512))
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    file_base64: Mapped[str] = mapped_column(Text)
    preview_json: Mapped[list | None] = mapped_column(JSONB)
    stats_json: Mapped[dict | None] = mapped_column(JSONB)
    changed_cells_json: Mapped[list | None] = mapped_column(JSONB)
    created_reason: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )

    created_by = relationship("User")


class AveonShipmentScheduleChangeEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "aveon_shipment_schedule_change_events"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_aveon_shipment_schedule_change_events_idempotency_key",
        ),
    )

    schedule_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("aveon_shipment_schedule_versions.id", ondelete="SET NULL"),
        index=True,
    )
    next_schedule_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("aveon_shipment_schedule_versions.id", ondelete="SET NULL"),
        index=True,
    )
    manager_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    manager_name: Mapped[str | None] = mapped_column(String(255), index=True)
    task_key: Mapped[str | None] = mapped_column(String(600), index=True)
    task_type: Mapped[str | None] = mapped_column(String(255))
    nomenclature: Mapped[str] = mapped_column(String(1024), index=True)
    country: Mapped[str | None] = mapped_column(String(64), index=True)
    supplier: Mapped[str | None] = mapped_column(String(512))
    original_dates_json: Mapped[list | None] = mapped_column(JSONB)
    add_batches_json: Mapped[list | None] = mapped_column(JSONB)
    quantity: Mapped[float | None] = mapped_column()
    manager_result: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True)
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    schedule_version = relationship(
        "AveonShipmentScheduleVersion",
        foreign_keys=[schedule_version_id],
    )
    next_schedule_version = relationship(
        "AveonShipmentScheduleVersion",
        foreign_keys=[next_schedule_version_id],
    )
    manager = relationship("User")
