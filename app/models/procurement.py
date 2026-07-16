from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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


__all__ = ["ProcurementCase", "ProcurementCaseEvent"]
