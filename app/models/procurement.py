from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ProcurementCaseStatus


class ProcurementCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "procurement_cases"
    __table_args__ = (
        UniqueConstraint("correlation_id", name="uq_procurement_cases_correlation_id"),
        UniqueConstraint("idempotency_key", name="uq_procurement_cases_idempotency_key"),
    )

    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    source_1c_ref: Mapped[str] = mapped_column(String(512), index=True)
    source_entity_set: Mapped[str | None] = mapped_column(String(255), index=True)
    source_database: Mapped[str | None] = mapped_column(String(128), index=True)
    source_number: Mapped[str | None] = mapped_column(String(128), index=True)
    source_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_status: Mapped[str | None] = mapped_column(String(128), index=True)
    source_data_version: Mapped[str | None] = mapped_column(String(128))
    source_content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    initiator_1c_ref: Mapped[str | None] = mapped_column(String(64))
    initiator_name: Mapped[str | None] = mapped_column(String(255))
    department_1c_ref: Mapped[str | None] = mapped_column(String(64), index=True)
    department_name: Mapped[str | None] = mapped_column(String(255))
    warehouse_1c_ref: Mapped[str | None] = mapped_column(String(64), index=True)
    warehouse_name: Mapped[str | None] = mapped_column(String(255))
    warehouse_from_1c_ref: Mapped[str | None] = mapped_column(String(64))
    warehouse_to_1c_ref: Mapped[str | None] = mapped_column(String(64))
    organization_1c_ref: Mapped[str | None] = mapped_column(String(64), index=True)
    priority_1c_ref: Mapped[str | None] = mapped_column(String(64))
    required_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    assigned_agents: Mapped[list | None] = mapped_column(JSONB)
    current_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        index=True,
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deviation_summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(64),
        default=ProcurementCaseStatus.NEW.value,
        index=True,
    )
    control_point: Mapped[str | None] = mapped_column(String(16), index=True)
    autonomy_level: Mapped[int] = mapped_column(Integer, default=0)
    current_agent_id: Mapped[str | None] = mapped_column(String(128), index=True)
    current_human_role: Mapped[str | None] = mapped_column(String(255), index=True)
    requested_operation: Mapped[str] = mapped_column(String(128), default="assess_need")
    idempotency_key: Mapped[str] = mapped_column(String(255), index=True)
    graph_version: Mapped[str] = mapped_column(String(64), default="0.1.0")
    rule_registry_version: Mapped[str | None] = mapped_column(String(64))
    case_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)
    latest_result: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list["ProcurementCaseEvent"]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="ProcurementCaseEvent.created_at",
    )
    positions: Mapped[list["ProcurementCasePosition"]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="ProcurementCasePosition.line_number",
    )


class ProcurementCasePosition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "procurement_case_positions"
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "line_id",
            name="uq_procurement_case_positions_case_id_line_id",
        ),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("procurement_cases.id", ondelete="CASCADE"),
        index=True,
    )
    line_id: Mapped[str] = mapped_column(String(128))
    line_number: Mapped[int] = mapped_column(Integer, default=0)
    nomenclature_id: Mapped[str] = mapped_column(String(64), index=True)
    nomenclature_name: Mapped[str | None] = mapped_column(String(512))
    characteristic_id: Mapped[str | None] = mapped_column(String(64))
    unit: Mapped[str | None] = mapped_column(String(64))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    required_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)

    case: Mapped[ProcurementCase] = relationship(back_populates="positions")


class ProcurementCaseEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "procurement_case_events"
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "idempotency_key",
            name="uq_procurement_case_events_case_id_idempotency_key",
        ),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("procurement_cases.id", ondelete="CASCADE"),
        index=True,
    )
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(128), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    actor_role: Mapped[str | None] = mapped_column(String(255))
    previous_status: Mapped[str | None] = mapped_column(String(64))
    new_status: Mapped[str | None] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    rule_refs: Mapped[list | None] = mapped_column(JSONB)
    payload: Mapped[dict | None] = mapped_column(JSONB)

    case: Mapped[ProcurementCase] = relationship(back_populates="events")


class ProcurementSourceSyncState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "procurement_source_sync_state"
    __table_args__ = (
        UniqueConstraint(
            "database_name",
            "source_type",
            name="uq_procurement_source_sync_state_db_source",
        ),
    )

    database_name: Mapped[str] = mapped_column(String(128), index=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_set: Mapped[str | None] = mapped_column(String(255))
    capability_status: Mapped[str] = mapped_column(
        String(64),
        default="unknown",
        index=True,
    )
    capability_message: Mapped[str | None] = mapped_column(Text)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    watermark_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    watermark_refs: Mapped[list | None] = mapped_column(JSONB)
    last_error: Mapped[str | None] = mapped_column(Text)
    documents_seen: Mapped[int] = mapped_column(Integer, default=0)
    cases_created: Mapped[int] = mapped_column(Integer, default=0)
    cases_updated: Mapped[int] = mapped_column(Integer, default=0)
    cases_skipped: Mapped[int] = mapped_column(Integer, default=0)


__all__ = [
    "ProcurementCase",
    "ProcurementCaseEvent",
    "ProcurementCasePosition",
    "ProcurementSourceSyncState",
]
