from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EskdMarkingDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "eskd_marking_documents"

    designation: Mapped[str | None] = mapped_column(String(128), index=True)
    source_filename: Mapped[str] = mapped_column(String(512))
    pages: Mapped[list | None] = mapped_column(JSONB)

    labels: Mapped[list["EskdMarkingLabel"]] = relationship(back_populates="document")


class EskdMarkingLabel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "eskd_marking_labels"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eskd_marking_documents.id", ondelete="CASCADE"),
        index=True,
    )
    check_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("eskd_check_runs.id", ondelete="SET NULL"),
        index=True,
    )
    is_rework: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    document_level: Mapped[list | None] = mapped_column(JSONB)
    page_level: Mapped[list | None] = mapped_column(JSONB)
    problem_report: Mapped[str | None] = mapped_column(Text)
    human_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("eskd_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    verified_by_login: Mapped[str | None] = mapped_column(String(64))
    verified_by_name: Mapped[str | None] = mapped_column(String(256))

    document: Mapped["EskdMarkingDocument"] = relationship(back_populates="labels")
