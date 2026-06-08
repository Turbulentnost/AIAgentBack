from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AgentBuilderPlanStatus, AgentBuilderPlanStepStatus


class AgentBuilderPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_builder_plans"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_builder_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[AgentBuilderPlanStatus] = mapped_column(default=AgentBuilderPlanStatus.DRAFT, index=True)
    created_by_agent: Mapped[bool] = mapped_column(default=True)

    session: Mapped["AgentBuilderSession"] = relationship(back_populates="plans")
    steps: Mapped[list["AgentBuilderPlanStep"]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="AgentBuilderPlanStep.step_order",
    )


class AgentBuilderPlanStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_builder_plan_steps"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_builder_plans.id", ondelete="CASCADE"),
        index=True,
    )
    step_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[AgentBuilderPlanStepStatus] = mapped_column(
        default=AgentBuilderPlanStepStatus.PENDING,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    plan: Mapped["AgentBuilderPlan"] = relationship(back_populates="steps")


if TYPE_CHECKING:
    from app.models.agent_builder_session import AgentBuilderSession
