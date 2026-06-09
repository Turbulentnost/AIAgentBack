from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin


class AgentBuilderAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_builder_attempts"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_builder_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, index=True)
    goal: Mapped[str | None] = mapped_column(Text)
    input_context: Mapped[dict | None] = mapped_column(JSONB)
    planned_actions: Mapped[list | None] = mapped_column(JSONB)
    executed_actions: Mapped[list | None] = mapped_column(JSONB)
    result_summary: Mapped[str | None] = mapped_column(Text)
    success: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    session: Mapped["AgentBuilderSession"] = relationship(back_populates="attempts")


if TYPE_CHECKING:
    from app.models.agent_builder_session import AgentBuilderSession
