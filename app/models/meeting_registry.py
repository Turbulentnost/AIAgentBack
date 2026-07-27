from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import MeetingRegistryEventType, MeetingRegistryStage


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
    participants: Mapped[list] = mapped_column(JSONB, default=list)
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
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    protocol_number: Mapped[str | None] = mapped_column(String(128), index=True)
    protocol_ref_key: Mapped[str | None] = mapped_column(String(36))
    protocol_draft_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    protocol_draft_celery_task_id: Mapped[str | None] = mapped_column(String(255), index=True)
    protocol_draft_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    protocol_draft_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    protocol_draft_error: Mapped[str | None] = mapped_column(Text)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    outlook_item_id: Mapped[str | None] = mapped_column(String(512))
    outlook_changekey: Mapped[str | None] = mapped_column(String(512))
    outlook_meeting_url: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    scheduled_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scheduled_meetings.id", ondelete="SET NULL"),
        unique=True,
        index=True,
    )
    series_occurrence_date: Mapped[date | None] = mapped_column(Date)

    approved_by: Mapped["User | None"] = relationship()
    scheduled_meeting: Mapped["ScheduledMeeting | None"] = relationship()
    events: Mapped[list["MeetingRegistryEvent"]] = relationship(
        back_populates="entry",
        order_by="MeetingRegistryEvent.occurred_at",
    )


class MeetingRegistryEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "meeting_registry_events"

    registry_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meeting_registry_entries.id", ondelete="CASCADE"),
        index=True,
    )
    memo_ref_key: Mapped[str] = mapped_column(String(36), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[MeetingRegistryEventType] = mapped_column(
        SAEnum(
            MeetingRegistryEventType,
            name="meetingregistryeventtype",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        index=True,
    )
    message: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    payload: Mapped[dict | None] = mapped_column(JSONB)

    entry: Mapped[MeetingRegistryEntry] = relationship(back_populates="events")
    actor: Mapped["User | None"] = relationship()


from app.models.scheduled_meeting import ScheduledMeeting  # noqa: E402
from app.models.user import User  # noqa: E402
