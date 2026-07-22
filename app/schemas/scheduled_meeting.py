from __future__ import annotations

import uuid
from datetime import date, time
from typing import Literal

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
from app.schemas.meeting import MeetingRegistryEventRead, MeetingRegistryItemRead
from app.services.scheduled_meeting_recurrence import (
    RecurrenceInput,
    build_recurrence_rule,
    default_series_end_date,
    format_recurrence_label,
    validate_recurrence_input,
)


class ScheduledMeetingParticipantRead(ORMModel):
    id: uuid.UUID
    position_id: uuid.UUID
    position_name: str | None = None
    department_id: uuid.UUID | None = Field(
        default=None,
        description="Устаревший alias position_id для совместимости с фронтом",
    )
    department_name: str | None = Field(
        default=None,
        description="Устаревший alias position_name для совместимости с фронтом",
    )
    sort_order: int
    is_required: bool


class ScheduledMeetingParticipantOptionRead(BaseModel):
    id: uuid.UUID
    name: str
    slug: str | None = None


class MeetingCategoryRead(ORMModel):
    id: uuid.UUID
    name: str
    sort_order: int
    is_active: bool


class ScheduledMeetingRolePositionRead(BaseModel):
    position_id: uuid.UUID
    position_name: str | None = None


class ScheduledMeetingRead(ORMModel):
    id: uuid.UUID
    title: str
    meeting_category_id: uuid.UUID
    meeting_category_name: str | None = None
    manager_position_id: uuid.UUID
    manager_position_name: str | None = None
    responsible_position_id: uuid.UUID
    responsible_position_name: str | None = None
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
    position_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = Field(
        default=None,
        description="Устаревший alias position_id для совместимости с фронтом",
    )
    sort_order: int = 0
    is_required: bool = True

    @model_validator(mode="after")
    def resolve_position_id(self) -> ScheduledMeetingParticipantCreate:
        if self.position_id is not None:
            return self
        if self.department_id is not None:
            return self.model_copy(update={"position_id": self.department_id})
        raise ValueError("Укажите position_id участника серии")


class ScheduledMeetingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    meeting_category_id: uuid.UUID
    manager_position_id: uuid.UUID
    responsible_position_id: uuid.UUID
    meeting_type: ScheduledMeetingType
    status: ScheduledMeetingStatus = ScheduledMeetingStatus.PLANNED
    recurrence: ScheduledMeetingRecurrencePayload
    series_start_date: date | None = Field(
        default=None,
        description="Срок серии (с); если не задан — из recurrence или сегодня",
    )
    series_end_date: date | None = Field(
        default=None,
        description="Срок серии (по); по умолчанию 31.12 года начала",
    )
    participants: list[ScheduledMeetingParticipantCreate] = Field(default_factory=list)
    comment: str | None = Field(default=None, max_length=4000)
    payload: dict | None = None

    @model_validator(mode="after")
    def validate_recurrence(self) -> ScheduledMeetingCreate:
        validate_recurrence_input(self.resolved_recurrence_input())
        return self

    def resolved_recurrence_input(self) -> RecurrenceInput:
        recurrence = self.recurrence
        start = self.series_start_date or recurrence.series_start_date or date.today()
        end = self.series_end_date or recurrence.series_end_date or default_series_end_date(
            year=start.year
        )
        return RecurrenceInput(
            frequency=recurrence.frequency,
            interval=recurrence.interval,
            time_local=recurrence.time_local,
            duration_minutes=recurrence.duration_minutes,
            series_start_date=start,
            series_end_date=end,
            monthly_mode=recurrence.monthly_mode,
            day_of_month=recurrence.day_of_month,
            weekday=recurrence.weekday,
            weekday_position=recurrence.weekday_position,
        )

    def resolved_payload(self) -> dict | None:
        payload = dict(self.payload or {})
        if self.comment and self.comment.strip():
            payload["comment"] = self.comment.strip()
        return payload or None

    def recurrence_label(self) -> str:
        return format_recurrence_label(self.resolved_recurrence_input())

    def recurrence_rule(self) -> dict:
        return build_recurrence_rule(self.resolved_recurrence_input())


class ScheduledMeetingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    meeting_category_id: uuid.UUID | None = None
    manager_position_id: uuid.UUID | None = None
    responsible_position_id: uuid.UUID | None = None
    meeting_type: ScheduledMeetingType | None = None
    status: ScheduledMeetingStatus | None = None
    recurrence: ScheduledMeetingRecurrencePayload | None = None
    series_start_date: date | None = Field(
        default=None,
        description="Срок серии (с); изменение пока не поддерживается",
    )
    series_end_date: date | None = Field(
        default=None,
        description="Срок серии (по)",
    )
    participants: list[ScheduledMeetingParticipantCreate] | None = None
    comment: str | None = Field(default=None, max_length=4000)
    payload: dict | None = None

    @model_validator(mode="after")
    def validate_recurrence(self) -> ScheduledMeetingUpdate:
        if self.recurrence is not None:
            validate_recurrence_input(self.recurrence.to_recurrence_input())
        return self


class ScheduledMeetingAppliedChangesRead(BaseModel):
    db_updated: bool
    outlook_updated: bool
    changes: list[str] = Field(default_factory=list)
    outlook_actions: list[str] = Field(default_factory=list)
    participants_added: list[str] = Field(default_factory=list)
    participants_removed: list[str] = Field(default_factory=list)


class ScheduledMeetingUpdateRead(BaseModel):
    series: ScheduledMeetingRead
    applied_changes: ScheduledMeetingAppliedChangesRead


class ScheduledMeetingOccurrenceRead(BaseModel):
    occurrence_date: date
    slot_start: str
    slot_end: str
    subject: str
    outlook_item_id: str | None = None
    outlook_meeting_url: str | None = None
    source: Literal["outlook", "rule", "none"] = "none"


class ScheduledMeetingDetailRead(BaseModel):
    series: ScheduledMeetingRead
    next_occurrence: ScheduledMeetingOccurrenceRead | None = None
    past_occurrences: list[ScheduledMeetingOccurrenceRead] = Field(default_factory=list)
    current_card: MeetingRegistryItemRead | None = None
    history: list[MeetingRegistryEventRead] = Field(default_factory=list)
