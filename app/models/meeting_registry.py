from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import MeetingRegistryStage


class MeetingRegistryEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "meeting_registry_entries"
    __table_args__ = (UniqueConstraint("memo_ref_key", name="uq_meeting_registry_memo_ref_key"),)

    memo_ref_key: Mapped[str] = mapped_column(String(36), index=True)
    memo_number: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str | None] = mapped_column(String(512))
    subject: Mapped[str | None] = mapped_column(String(512))
    location: Mapped[str | None] = mapped_column(String(512))
    initiator_name: Mapped[str | None] = mapped_column(String(256))
    manager_name: Mapped[str | None] = mapped_column(String(256))
    participants_count: Mapped[int] = mapped_column(Integer, default=0)
    slot_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    slot_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stage: Mapped[MeetingRegistryStage] = mapped_column(
        SAEnum(
            MeetingRegistryStage,
            name="meetingregistrystage",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        default=MeetingRegistryStage.INVITATIONS_SENT,
        index=True,
    )
    invitations_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    protocol_number: Mapped[str | None] = mapped_column(String(128), index=True)
    protocol_ref_key: Mapped[str | None] = mapped_column(String(36))
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    outlook_item_id: Mapped[str | None] = mapped_column(String(512))
    outlook_changekey: Mapped[str | None] = mapped_column(String(512))
    outlook_meeting_url: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)

    approved_by: Mapped["User | None"] = relationship()


from app.models.user import User  # noqa: E402
