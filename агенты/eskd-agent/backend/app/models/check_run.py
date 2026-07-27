from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EskdCheckRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "eskd_check_runs"

    job_id: Mapped[str] = mapped_column(String(64), index=True)
    original_filename: Mapped[str | None] = mapped_column(String(512))
    designation: Mapped[str | None] = mapped_column(String(128), index=True)
    content_type: Mapped[str | None] = mapped_column(String(128))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    file_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    pages_count: Mapped[int | None] = mapped_column(Integer)
    check_params: Mapped[dict | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(String(256))
    adapter: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), default="completed", index=True)
    total_errors: Mapped[int] = mapped_column(Integer, default=0)
    total_warnings: Mapped[int] = mapped_column(Integer, default=0)
    human_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_result: Mapped[dict | None] = mapped_column(JSONB)
    gost_summary: Mapped[dict | None] = mapped_column(JSONB)
    document_key: Mapped[str | None] = mapped_column(String(128), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("eskd_check_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("eskd_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_login: Mapped[str | None] = mapped_column(String(64))
    created_by_name: Mapped[str | None] = mapped_column(String(256))
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("eskd_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    verified_by_login: Mapped[str | None] = mapped_column(String(64))
    verified_by_name: Mapped[str | None] = mapped_column(String(256))
