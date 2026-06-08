from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import BrowserRunStatus


class BrowserRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "browser_runs"

    requested_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"),
        index=True,
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        index=True,
    )
    url: Mapped[str] = mapped_column(String(2048))
    method: Mapped[str] = mapped_column(String(16), default="GET")
    extract_mode: Mapped[str] = mapped_column(String(32), default="text", index=True)
    status: Mapped[BrowserRunStatus] = mapped_column(default=BrowserRunStatus.PENDING, index=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    title: Mapped[str | None] = mapped_column(String(512))
    result_text: Mapped[str | None] = mapped_column(Text)
    result_html: Mapped[str | None] = mapped_column(Text)
    result_tables: Mapped[list | None] = mapped_column(JSONB)
    screenshot_object_name: Mapped[str | None] = mapped_column(String(1024))
    error_message: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
