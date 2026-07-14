from __future__ import annotations

import uuid
from datetime import date, time

from pydantic import BaseModel, Field, model_validator

from app.models.enums import (
    ScheduledMeetingFrequency,
    ScheduledMeetingMonthlyMode,
    ScheduledMeetingStatus,
    ScheduledMeetingType,
    ScheduledMeetingWeekday,
    ScheduledMeetingWeekdayPosition,
)
from app.schemas.common import ORMModel
from app.services.scheduled_meeting_recurrence import (
    RecurrenceInput,
    build_recurrence_rule,
    default_series_end_date,
    format_recurrence_label,
    validate_recurrence_input,
)


class ScheduledMeetingParticipantRead(ORMModel):
    id: uuid.UUID
    department_id: uuid.UUID
    department_name: str | None = None
    sort_order: int
    is_required: bool


class ScheduledMeetingRead(ORMModel):
    id: uuid.UUID
    title: str
    meeting_type: ScheduledMeetingType
    status: ScheduledMeetingStatus
    time_local: time
    duration_minutes: int
    frequency: ScheduledMeetingFrequency
    interval: int
    monthly_mode: ScheduledMeetingMonthlyMode | None = None
    day_of_month: int | None = None
    weekday: ScheduledMeetingWeekday | None = None
    weekday_position: ScheduledMeetingWeekdayPosition | None = None
    series_start_date: date
    series_end_date: date
    recurrence_label: str
    recurrence_rule: dict
    outlook_series_id: str | None = None
    outlook_changekey: str | None = None
    outlook_meeting_url: str | None = None
    payload: dict | None = None
    participants: list[ScheduledMeetingParticipantRead] = Field(default_factory=list)


class ScheduledMeetingRecurrencePayload(BaseModel):
    frequency: ScheduledMeetingFrequency
    interval: int = 1
    time_local: time
    duration_minutes: int = 60
    monthly_mode: ScheduledMeetingMonthlyMode | None = None
    day_of_month: int | None = None
    weekday: ScheduledMeetingWeekday | None = None
    weekday_position: ScheduledMeetingWeekdayPosition | None = None
    series_start_date: date | None = None
    series_end_date: date | None = None

    def to_recurrence_input(self) -> RecurrenceInput:
        start = self.series_start_date or date.today()
        end = self.series_end_date or default_series_end_date(year=start.year)
        return RecurrenceInput(
            frequency=self.frequency,
            interval=self.interval,
            time_local=self.time_local,
            duration_minutes=self.duration_minutes,
            series_start_date=start,
            series_end_date=end,
            monthly_mode=self.monthly_mode,
            day_of_month=self.day_of_month,
            weekday=self.weekday,
            weekday_position=self.weekday_position,
        )


class ScheduledMeetingParticipantCreate(BaseModel):
    department_id: uuid.UUID
    sort_order: int = 0
    is_required: bool = True


class ScheduledMeetingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    meeting_type: ScheduledMeetingType
    status: ScheduledMeetingStatus = ScheduledMeetingStatus.PLANNED
    recurrence: ScheduledMeetingRecurrencePayload
    participants: list[ScheduledMeetingParticipantCreate] = Field(default_factory=list)
    payload: dict | None = None

    @model_validator(mode="after")
    def validate_recurrence(self) -> ScheduledMeetingCreate:
        validate_recurrence_input(self.recurrence.to_recurrence_input())
        return self

    def recurrence_label(self) -> str:
        return format_recurrence_label(self.recurrence.to_recurrence_input())

    def recurrence_rule(self) -> dict:
        return build_recurrence_rule(self.recurrence.to_recurrence_input())


class ScheduledMeetingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    meeting_type: ScheduledMeetingType | None = None
    status: ScheduledMeetingStatus | None = None
    recurrence: ScheduledMeetingRecurrencePayload | None = None
    participants: list[ScheduledMeetingParticipantCreate] | None = None
    payload: dict | None = None

    @model_validator(mode="after")
    def validate_recurrence(self) -> ScheduledMeetingUpdate:
        if self.recurrence is not None:
            validate_recurrence_input(self.recurrence.to_recurrence_input())
        return self
