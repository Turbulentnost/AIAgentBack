from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentBuilderSandboxRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_builder_sandbox_runs"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_builder_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    test_query: Mapped[str | None] = mapped_column(Text)
    final_answer: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict | None] = mapped_column(JSONB)
    executed_graph: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()

    steps: Mapped[list["AgentBuilderSandboxStep"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="AgentBuilderSandboxStep.order_index",
    )


class AgentBuilderSandboxStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_builder_sandbox_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_builder_sandbox_runs.id", ondelete="CASCADE"),
        index=True,
    )
    order_index: Mapped[int] = mapped_column(Integer, default=0, index=True)
    title: Mapped[str | None] = mapped_column(String(512))
    capability: Mapped[str | None] = mapped_column(String(128))
    tool_name: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    request: Mapped[dict | None] = mapped_column(JSONB)
    result_summary: Mapped[dict | None] = mapped_column(JSONB)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()

    run: Mapped[AgentBuilderSandboxRun] = relationship(back_populates="steps")


if TYPE_CHECKING:
    pass
