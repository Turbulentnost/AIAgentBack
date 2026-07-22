from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MeetingCategory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "meeting_categories"
    __table_args__ = (UniqueConstraint("name", name="uq_meeting_categories_name"),)

    name: Mapped[str] = mapped_column(String(256), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    scheduled_meetings: Mapped[list["ScheduledMeeting"]] = relationship(
        back_populates="meeting_category",
    )
