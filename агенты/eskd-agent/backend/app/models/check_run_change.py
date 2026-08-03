from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EskdCheckRunChange(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "eskd_check_run_changes"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eskd_check_runs.id", ondelete="CASCADE"),
        index=True,
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("eskd_check_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("eskd_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    changed_by_login: Mapped[str | None] = mapped_column(String(64))
    changed_by_name: Mapped[str | None] = mapped_column(String(256))
    change_type: Mapped[str] = mapped_column(String(32), index=True)
    summary: Mapped[str] = mapped_column(Text)
    diff: Mapped[dict | None] = mapped_column(JSONB)
