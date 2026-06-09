from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AgentBlueprintStatus


class AgentBlueprint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_blueprints"

    name: Mapped[str] = mapped_column(String(255), index=True)
    code: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    created_by_builder_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_builder_sessions.id", ondelete="SET NULL"),
        index=True,
    )
    status: Mapped[AgentBlueprintStatus] = mapped_column(
        default=AgentBlueprintStatus.DRAFT,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    agent_type: Mapped[str | None] = mapped_column(String(64))
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"),
        index=True,
    )
    input_schema: Mapped[dict | None] = mapped_column(JSONB)
    output_schema: Mapped[dict | None] = mapped_column(JSONB)
    tools: Mapped[list | None] = mapped_column(JSONB)
    knowledge_bases: Mapped[list | None] = mapped_column(JSONB)
    workflow_graph: Mapped[dict | None] = mapped_column(JSONB)
    human_approval_rules: Mapped[list | None] = mapped_column(JSONB)
    prompts: Mapped[dict | None] = mapped_column(JSONB)
    test_cases: Mapped[list | None] = mapped_column(JSONB)
    report_template: Mapped[dict | None] = mapped_column(JSONB)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    session: Mapped["AgentBuilderSession | None"] = relationship(back_populates="blueprints")


if TYPE_CHECKING:
    from app.models.agent_builder_session import AgentBuilderSession
