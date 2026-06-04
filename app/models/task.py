from __future__ import annotations

import uuid
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ConfidenceLevel, TaskStatus

class Task(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(default=TaskStatus.PENDING, index=True)
    task_type: Mapped[str | None] = mapped_column(String(128), index=True)
    input_payload: Mapped[dict | None] = mapped_column(JSONB)
    final_result: Mapped[dict | None] = mapped_column(JSONB)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    steps: Mapped[list["TaskStep"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    results: Mapped[list["TaskResult"]] = relationship(back_populates="task", cascade="all, delete-orphan")

class TaskStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "task_steps"
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    order: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[TaskStatus] = mapped_column(default=TaskStatus.PENDING)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    task: Mapped[Task] = relationship(back_populates="steps")

class TaskResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "task_results"
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)
    findings: Mapped[list | None] = mapped_column(JSONB)
    data_confidence: Mapped[ConfidenceLevel | None] = mapped_column()
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_output: Mapped[dict | None] = mapped_column(JSONB)
    task: Mapped[Task] = relationship(back_populates="results")
