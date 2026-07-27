from __future__ import annotations

import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, Field

from app.models.enums import ScheduledMeetingWeekday
from app.schemas.common import ORMModel
from app.schemas.scheduled_meeting import ScheduledMeetingRead


class AppNotificationRead(ORMModel):
    id: uuid.UUID
    type: str
    title: str
    body: str
    entity_key: str
    payload: dict | None = None
    read_at: datetime | None = None
    opened_at: datetime | None = None
    resolved_at: datetime | None = None
    created_at: datetime | None = None


class AppNotificationListRead(BaseModel):
    items: list[AppNotificationRead] = Field(default_factory=list)
    unread_count: int = 0


class TurboProjectRgParticipantProposal(BaseModel):
    user_id: uuid.UUID
    fio: str
    email: str
    role: str | None = None
    position_name: str | None = None


class TurboProjectRgWeeklySlotProposal(BaseModel):
    weekday: ScheduledMeetingWeekday
    time_local: time
    duration_minutes: int = 60
    slot_start: str | None = None
    coverage_ratio: float | None = None
    fallback: bool = False


class TurboProjectRgSeriesProposal(BaseModel):
    file_id: int
    project_name: str
    one_c_ref_key: str | None = None
    nomer_proekta: str | None = None
    status_proekta: str | None = None
    title: str
    meeting_category_name: str
    series_start_date: date
    series_end_date: date
    recurrence_label: str
    weekly_slot: TurboProjectRgWeeklySlotProposal
    manager: TurboProjectRgParticipantProposal
    responsible: TurboProjectRgParticipantProposal
    participants: list[TurboProjectRgParticipantProposal] = Field(default_factory=list)


class AppNotificationOpenRead(BaseModel):
    notification: AppNotificationRead
    proposal: TurboProjectRgSeriesProposal | None = None


class AppNotificationAcceptRequest(BaseModel):
    weekday: ScheduledMeetingWeekday | None = None
    time_local: time | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=480)


class AppNotificationAcceptRead(BaseModel):
    notification: AppNotificationRead
    scheduled_meeting: ScheduledMeetingRead
