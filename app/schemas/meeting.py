from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class MeetingDashboardItem(BaseModel):
    ref_key: str | None = None
    number: str | None = None
    title: str | None = None
    status: str | None = None
    status_label: str | None = None
    meeting_type: str | None = None
    meeting_type_label: str | None = None
    document_date: str | None = None
    scheduled_label: str | None = None
    meeting_date: str | None = None
    desired_meeting_date: str | None = None
    meeting_start: str | None = None
    meeting_end: str | None = None
    participants_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    location: str | None = None
    comment: str | None = None
    subject: str | None = None


class MeetingPersonRead(BaseModel):
    ref_key: str | None = None
    full_name: str | None = None
    department: str | None = None
    position: str | None = None


class MeetingParticipantDetailRead(BaseModel):
    ref_key: str | None = None
    full_name: str | None = None
    department: str | None = None


class MeetingValidationCheckRead(BaseModel):
    field: str
    label: str
    severity: str
    message: str
    passed: bool


class MeetingHistoryEventRead(BaseModel):
    timestamp: str
    message: str


class MeetingApplicationRead(BaseModel):
    initiator: MeetingPersonRead | None = None
    manager: MeetingPersonRead | None = None
    participants: list[MeetingParticipantDetailRead] = Field(default_factory=list)
    participants_count: int = 0
    agenda: str | None = None
    scheduled_label: str | None = None
    document_date: str | None = None
    meeting_start: str | None = None
    meeting_end: str | None = None
    duration_minutes: int | None = None
    location: str | None = None
    meeting_type: str | None = None
    meeting_type_label: str | None = None
    priority: str | None = None


class MeetingMemoDetailRead(BaseModel):
    ref_key: str | None = None
    number: str | None = None
    title: str | None = None
    status: str | None = None
    status_label: str | None = None
    queue: MeetingDashboardItem
    application: MeetingApplicationRead
    validation_checks: list[MeetingValidationCheckRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    history: list[MeetingHistoryEventRead] = Field(default_factory=list)
    agent_recommendation: str | None = None


class MeetingLoginContext(BaseModel):
    date: str
    unapproved: list[MeetingDashboardItem] = Field(default_factory=list)
    today: list[MeetingDashboardItem] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    fetched_at: datetime
    error: str | None = None


class MeetingPermissionsRead(BaseModel):
    can_access_agent: bool
    can_manage_meetings: bool


class MeetingMemoRead(BaseModel):
    ref_key: str | None = None
    number: str | None = None
    date: str | None = None
    subject: str | None = None
    meeting_type: str | None = None
    participant_fio: list[str] = Field(default_factory=list)


class MeetingParticipantRead(BaseModel):
    fio: str
    email: str | None = None
    found: bool = False


class MeetingSlotRead(BaseModel):
    start: str
    end: str
    confidence: float


class MeetingRoomRead(BaseModel):
    name: str
    email: str | None = None
    available: bool | None = None


class MeetingInviteDraftRead(BaseModel):
    subject: str
    start: str
    end: str
    location: str
    attendees: list[str]
    body: str


class MeetingRunCreate(BaseModel):
    memo_ref_key: uuid.UUID | None = None
    memo_number: str | None = None
    meeting_type: str | None = None
    subject: str | None = None
    planned_start: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=480)
    participant_fio: list[str] = Field(default_factory=list)
    room_name: str | None = None
    initiator_comment: str | None = None
    title: str | None = Field(default=None, max_length=512)


class MeetingRunRead(BaseModel):
    task_id: uuid.UUID
    status: str
    celery_task_id: str | None = None
    requires_human_review: bool = True


class MeetingRunResultRead(BaseModel):
    task_id: uuid.UUID
    status: str
    summary: str | None = None
    result: dict | None = None
    requires_human_review: bool = False
    error_message: str | None = None


class MeetingSlotsRequest(BaseModel):
    memo_ref_key: uuid.UUID | None = None
    memo_number: str | None = None
    participant_fio: list[str] = Field(default_factory=list)
    planned_start: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=480)


class MeetingRoomsRequest(BaseModel):
    slot_start: str
    slot_end: str | None = None
    room_name: str | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=480)


class MeetingInvitePreviewRequest(BaseModel):
    memo_ref_key: uuid.UUID | None = None
    memo_number: str | None = None
    participant_fio: list[str] = Field(default_factory=list)
    slot_start: str
    slot_end: str
    room_name: str | None = None
    subject: str | None = None


class MeetingInviteSendRequest(BaseModel):
    subject: str
    start: str
    end: str
    location: str = ""
    attendees: list[str] = Field(..., min_length=1)
    body: str = ""
