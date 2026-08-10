from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ShiftCompletionReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "shift_completion_reports"
    __table_args__ = (
        UniqueConstraint(
            "manager_user_id",
            "report_date",
            name="uq_shift_completion_reports_manager_date",
        ),
        UniqueConstraint(
            "manager_name",
            "report_date",
            name="uq_shift_completion_reports_manager_name_date",
        ),
    )

    manager_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    manager_name: Mapped[str] = mapped_column(String(255), index=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    stats_json: Mapped[dict] = mapped_column(JSONB)
    tasks_json: Mapped[list] = mapped_column(JSONB)
    incomplete_reasons_json: Mapped[dict] = mapped_column(JSONB)
    email_sent_to: Mapped[str] = mapped_column(String(512))
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    manager = relationship("User")
