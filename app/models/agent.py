from __future__ import annotations

import uuid
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AgentStatus

class Agent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agents"
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    purpose: Mapped[str | None] = mapped_column(Text)
    status: Mapped[AgentStatus] = mapped_column(default=AgentStatus.DRAFT, index=True)
    input_schema: Mapped[dict | None] = mapped_column(JSONB)
    output_schema: Mapped[dict | None] = mapped_column(JSONB)
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    versions: Mapped[list["AgentVersion"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    tools: Mapped[list["AgentTool"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    user_access: Mapped[list["UserAgent"]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
    )
    department_access: Mapped[list["DepartmentAgent"]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
    )

class AgentVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_versions"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[dict | None] = mapped_column(JSONB)
    changelog: Mapped[str | None] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    agent: Mapped[Agent] = relationship(back_populates="versions")
    prompts: Mapped[list["AgentPrompt"]] = relationship(back_populates="agent_version", cascade="all, delete-orphan")

class AgentPrompt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_prompts"
    agent_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_versions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(64), default="system")
    content: Mapped[str] = mapped_column(Text)
    agent_version: Mapped[AgentVersion] = relationship(back_populates="prompts")

class AgentTool(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_tools"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(255), index=True)
    config: Mapped[dict | None] = mapped_column(JSONB)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    agent: Mapped[Agent] = relationship(back_populates="tools")

class ToolCall(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tool_calls"
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    tool_name: Mapped[str] = mapped_column(String(255), index=True)
    request: Mapped[dict | None] = mapped_column(JSONB)
    response: Mapped[dict | None] = mapped_column(JSONB)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
