from __future__ import annotations

import uuid
from datetime import date, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ScheduledMeetingFrequency,
    ScheduledMeetingMonthlyMode,
    ScheduledMeetingStatus,
    ScheduledMeetingType,
    ScheduledMeetingWeekday,
    ScheduledMeetingWeekdayPosition,
)


def _enum_values(enum_cls: type) -> list[str]:
    return [item.value for item in enum_cls]


class ScheduledMeeting(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scheduled_meetings"
    __table_args__ = (
        CheckConstraint("interval >= 1", name="ck_scheduled_meetings_interval_positive"),
        CheckConstraint(
            "series_end_date >= series_start_date",
            name="ck_scheduled_meetings_series_range",
        ),
        CheckConstraint(
            "(frequency != 'weekly') OR (weekday IS NOT NULL)",
            name="ck_scheduled_meetings_weekly_weekday",
        ),
        CheckConstraint(
            "(frequency != 'monthly') OR (monthly_mode IS NOT NULL)",
            name="ck_scheduled_meetings_monthly_mode",
        ),
        CheckConstraint(
            "(monthly_mode IS DISTINCT FROM 'by_day_of_month') OR (day_of_month IS NOT NULL)",
            name="ck_scheduled_meetings_monthly_day",
        ),
        CheckConstraint(
            "(monthly_mode IS DISTINCT FROM 'by_weekday_position') "
            "OR (weekday IS NOT NULL AND weekday_position IS NOT NULL)",
            name="ck_scheduled_meetings_monthly_weekday_position",
        ),
    )

    title: Mapped[str] = mapped_column(String(512))
    meeting_type: Mapped[ScheduledMeetingType] = mapped_column(
        SAEnum(
            ScheduledMeetingType,
            name="scheduledmeetingtype",
            values_callable=_enum_values,
        ),
        index=True,
    )
    status: Mapped[ScheduledMeetingStatus] = mapped_column(
        SAEnum(
            ScheduledMeetingStatus,
            name="scheduledmeetingstatus",
            values_callable=_enum_values,
        ),
        default=ScheduledMeetingStatus.PLANNED,
        index=True,
    )

    time_local: Mapped[time] = mapped_column(Time)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)

    frequency: Mapped[ScheduledMeetingFrequency] = mapped_column(
        SAEnum(
            ScheduledMeetingFrequency,
            name="scheduledmeetingfrequency",
            values_callable=_enum_values,
        ),
        index=True,
    )
    interval: Mapped[int] = mapped_column(Integer, default=1)
    monthly_mode: Mapped[ScheduledMeetingMonthlyMode | None] = mapped_column(
        SAEnum(
            ScheduledMeetingMonthlyMode,
            name="scheduledmeetingmonthlymode",
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weekday: Mapped[ScheduledMeetingWeekday | None] = mapped_column(
        SAEnum(
            ScheduledMeetingWeekday,
            name="scheduledmeetingweekday",
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    weekday_position: Mapped[ScheduledMeetingWeekdayPosition | None] = mapped_column(
        SAEnum(
            ScheduledMeetingWeekdayPosition,
            name="scheduledmeetingweekdayposition",
            values_callable=_enum_values,
        ),
        nullable=True,
    )

    series_start_date: Mapped[date] = mapped_column(Date)
    series_end_date: Mapped[date] = mapped_column(Date)

    recurrence_label: Mapped[str] = mapped_column(String(256))
    recurrence_rule: Mapped[dict] = mapped_column(JSONB)

    outlook_series_id: Mapped[str | None] = mapped_column(String(512))
    outlook_changekey: Mapped[str | None] = mapped_column(String(512))
    outlook_meeting_url: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)

    participants: Mapped[list["ScheduledMeetingParticipant"]] = relationship(
        back_populates="meeting",
        order_by="ScheduledMeetingParticipant.sort_order",
        cascade="all, delete-orphan",
    )


class ScheduledMeetingParticipant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scheduled_meeting_participants"
    __table_args__ = (
        UniqueConstraint(
            "scheduled_meeting_id",
            "department_id",
            name="uq_scheduled_meeting_participant",
        ),
    )

    scheduled_meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scheduled_meetings.id", ondelete="CASCADE"),
        index=True,
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"),
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True)

    meeting: Mapped[ScheduledMeeting] = relationship(back_populates="participants")
    department: Mapped["Department"] = relationship()


from app.models.user import Department  # noqa: E402
