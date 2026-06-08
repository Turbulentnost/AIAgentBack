from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AgentBuilderSessionStatus


class AgentBuilderSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_builder_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    goal: Mapped[str] = mapped_column(Text)
    current_stage: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[AgentBuilderSessionStatus] = mapped_column(
        default=AgentBuilderSessionStatus.DRAFT,
        index=True,
    )
    collected_requirements: Mapped[dict | None] = mapped_column(JSONB)
    validation_result: Mapped[dict | None] = mapped_column(JSONB)
    proposed_agent_structure: Mapped[dict | None] = mapped_column(JSONB)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    plans: Mapped[list["AgentBuilderPlan"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )
    attempts: Mapped[list["AgentBuilderAttempt"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )
    blueprints: Mapped[list["AgentBlueprint"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )


if TYPE_CHECKING:
    from app.models.agent_builder_attempt import AgentBuilderAttempt
    from app.models.agent_builder_plan import AgentBuilderPlan
    from app.models.agent_blueprint import AgentBlueprint
