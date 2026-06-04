from __future__ import annotations

import uuid
from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

class DataSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "data_sources"
    name: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    owner_department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    permissions: Mapped[list["SourcePermission"]] = relationship(back_populates="data_source", cascade="all, delete-orphan")

class SourcePermission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "source_permissions"
    data_source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), index=True)
    access_level: Mapped[str] = mapped_column(String(32), default="read")
    allowed_fields: Mapped[dict | None] = mapped_column(JSONB)
    data_source: Mapped[DataSource] = relationship(back_populates="permissions")
