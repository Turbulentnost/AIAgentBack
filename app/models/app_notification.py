from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AppNotification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "app_notifications"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "entity_key",
            name="uq_app_notifications_user_id_entity_key",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    type: Mapped[str] = mapped_column(
        String(64),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text)
    entity_key: Mapped[str] = mapped_column(String(255), index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    user = relationship("User", lazy="selectin")
