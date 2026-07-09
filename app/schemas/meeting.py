from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.meeting_duration import normalize_request_duration_minutes


class MeetingDashboardItem(BaseModel):
    ref_key: str | None = None
    number: str | None = None
    title: str | None = None
    status: str | None = None
    status_label: str | None = None
    meeting_type: str | None = None
    meeting_type_label: str | None = None
    document_date: str | None = None
    document_date_label: str | None = None
    scheduled_label: str | None = None
    meeting_date: str | None = None
    desired_meeting_date: str | None = None
    meeting_start: str | None = None
    meeting_end: str | None = None
    participants_count: int = 0
    participant_names: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    location: str | None = None
    comment: str | None = None
    subject: str | None = None
    initiator: MeetingPersonRead | None = None
    manager: MeetingPersonRead | None = None
    psd_level: bool = False


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


class MeetingStoChecklistItemRead(BaseModel):
    field: str
    label: str
    passed: bool
    message: str


class MeetingStoIssueRead(BaseModel):
    field: str
    message: str


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
    document_date_label: str | None = None
    meeting_start: str | None = None
    meeting_end: str | None = None
    duration_minutes: int | None = None
    location: str | None = None
    invite_location: str | None = None
    meeting_type: str | None = None
    meeting_type_label: str | None = None
    priority: str | None = None
    psd_level: bool = False


class MeetingMemoDetailRead(BaseModel):
    ref_key: str | None = None
    number: str | None = None
    title: str | None = None
    status: str | None = None
    status_label: str | None = None
    document_date: str | None = None
    document_date_label: str | None = None
    queue: MeetingDashboardItem
    application: MeetingApplicationRead
    validation_checks: list[MeetingValidationCheckRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    history: list[MeetingHistoryEventRead] = Field(default_factory=list)
    agent_recommendation: str | None = None
    sto_ready: bool = False
    auto_approve_allowed: bool = False
    sto_issues: list[MeetingStoIssueRead] = Field(default_factory=list)
    sto_checklist: list[MeetingStoChecklistItemRead] = Field(default_factory=list)


class MeetingLoginContext(BaseModel):
    date: str
    unapproved: list[MeetingDashboardItem] = Field(default_factory=list)
    today: list[MeetingDashboardItem] = Field(default_factory=list)
    items: list[MeetingDashboardItem] = Field(default_factory=list)
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


class MeetingSlotCoverageRead(BaseModel):
    free: int
    total: int
    ratio: float = Field(ge=0, le=1)
    weighted_ratio: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Доля свободных с учётом весов (инициатор/руководитель/директор = 3, участник = 1)",
    )
    required_ok: bool


class MeetingSlotConflictRead(BaseModel):
    fio: str | None = None
    email: str
    role: str | None = None
    role_label: str | None = None
    event_start: str | None = None
    event_end: str | None = None
    event_subject: str | None = None
    event_label: str | None = Field(
        default=None,
        description="Тема встречи или «Занят», если subject недоступен",
    )
    event_time_label: str | None = Field(
        default=None,
        description="Интервал конфликта в формате «14.07.2026, 09:00–09:30»",
    )
    organizer: str | None = None
    busy_type: str | None = None
    movability: Literal["high", "medium", "low"] = "medium"
    movability_reason: Literal[
        "tentative",
        "busy",
        "oof",
        "protected_subject",
        "unknown_interval",
    ] | None = None
    source: Literal["calendar", "freebusy", "interval", "company_calendar"] | None = None
    can_auto_reschedule: bool = False
    reschedule_hint_start: str | None = None
    reschedule_hint_end: str | None = None
    reschedule_hint_label: str | None = None
    event_attendees: list[str] = Field(
        default_factory=list,
        description="E-mail участников конфликтующей встречи",
    )
    event_attendee_names: list[str] = Field(
        default_factory=list,
        description="ФИО участников встречи (или e-mail, если ФИО неизвестно)",
    )


class MeetingSlotBlockingEventRead(BaseModel):
    event_start: str | None = None
    event_end: str | None = None
    event_subject: str | None = None
    event_label: str | None = None
    event_time_label: str | None = Field(
        default=None,
        description="Интервал конфликта в формате «14.07.2026, 09:00–09:30»",
    )
    organizer: str | None = None
    busy_type: str | None = None
    movability: Literal["high", "medium", "low"] = "medium"
    movability_reason: Literal[
        "tentative",
        "busy",
        "oof",
        "protected_subject",
        "unknown_interval",
    ] | None = None
    source: Literal["calendar", "freebusy", "interval", "company_calendar"] | None = None
    reschedule_hint_start: str | None = None
    reschedule_hint_end: str | None = None
    reschedule_hint_label: str | None = None
    event_attendees: list[str] = Field(
        default_factory=list,
        description="E-mail участников конфликтующей встречи",
    )
    event_attendee_names: list[str] = Field(
        default_factory=list,
        description="ФИО участников встречи (или e-mail, если ФИО неизвестно)",
    )


class MeetingSlotParticipantStatusRead(BaseModel):
    fio: str
    email: str | None = None
    role: str
    role_label: str
    status: Literal["free", "busy", "unknown"]
    blocking_events: list[MeetingSlotBlockingEventRead] = Field(default_factory=list)
    calendar_access_error: str | None = Field(
        default=None,
        description="Ошибка чтения календаря (Free/Busy при этом может быть доступен)",
    )


class MeetingQuorumSlotRead(BaseModel):
    slot: MeetingSlotRead
    slot_label: str | None = None
    coverage: MeetingSlotCoverageRead
    conflicts: list[MeetingSlotConflictRead] = Field(default_factory=list)
    free_attendees: list[str] = Field(default_factory=list)
    busy_attendees: list[str] = Field(default_factory=list)
    free_attendee_names: list[str] = Field(
        default_factory=list,
        description="ФИО свободных участников (порядок соответствует free_attendees)",
    )
    busy_attendee_names: list[str] = Field(
        default_factory=list,
        description="ФИО занятых участников (порядок соответствует busy_attendees)",
    )
    verified: bool = False
    impact_score: float | None = Field(
        default=None,
        description="Стоимость слота: меньше — лучше (покрытие + должности + переносы)",
    )
    busy_weight_cost: float | None = Field(
        default=None,
        description="Суммарный вес занятых участников",
    )
    reschedule_count: int = Field(
        default=0,
        description="Число конфликтующих встреч, которые потенциально нужно переносить",
    )
    easy_reschedule_count: int = Field(
        default=0,
        description="Конфликты с высокой переносимостью (Tentative и т.п.)",
    )
    low_movability_count: int = Field(
        default=0,
        description="Конфликты с низкой переносимостью",
    )


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
    duration_minutes: int | None = None
    participant_fio: list[str] = Field(default_factory=list)
    room_name: str | None = None
    initiator_comment: str | None = None
    title: str | None = Field(default=None, max_length=512)

    @field_validator("duration_minutes", mode="before")
    @classmethod
    def normalize_duration(cls, value: object) -> int | None:
        return normalize_request_duration_minutes(value)


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
    duration_minutes: int | None = None

    @field_validator("duration_minutes", mode="before")
    @classmethod
    def normalize_duration(cls, value: object) -> int | None:
        return normalize_request_duration_minutes(value)


class MeetingAgentSlotPreviewRequest(BaseModel):
    duration_minutes: int | None = None

    @field_validator("duration_minutes", mode="before")
    @classmethod
    def normalize_duration(cls, value: object) -> int | None:
        return normalize_request_duration_minutes(value)


class MeetingAgentSlotDetailRequest(BaseModel):
    slot_start: str = Field(description="Начало выбранного слота (ISO или YYYY-MM-DD HH:MM)")
    slot_end: str = Field(description="Конец выбранного слота")
    duration_minutes: int | None = Field(
        default=None,
        description="Длительность совещания; если не указана — из slot_end − slot_start",
    )

    @field_validator("duration_minutes", mode="before")
    @classmethod
    def normalize_duration(cls, value: object) -> int | None:
        return normalize_request_duration_minutes(value)


class MeetingSlotRoomStatusRead(BaseModel):
    name: str
    email: str | None = None
    status: Literal["free", "busy", "unknown"]
    status_label: str
    available: bool | None = None
    calendar_access_error: str | None = Field(
        default=None,
        description="Ошибка проверки календаря переговорной",
    )


class MeetingAgentSlotDetailRead(BaseModel):
    memo_ref_key: str
    slot_start: str
    slot_end: str
    slot_label: str
    duration_minutes: int
    participants: list[MeetingSlotParticipantStatusRead] = Field(default_factory=list)
    room: MeetingSlotRoomStatusRead | None = None
    error: str | None = None
    error_stage: str | None = Field(
        default=None,
        description="participants | email | calendar | slot — этап сбоя",
    )


class MeetingAttendeeRead(BaseModel):
    fio: str
    email: str | None = None
    role: str
    role_label: str
    found: bool = False
    weight: float = Field(
        default=1.0,
        ge=0,
        description="Вес участника при quorum-поиске (инициатор/руководитель/директор выше)",
    )
    required_for_slot: bool = Field(
        default=True,
        description="Все участники обязательны для назначения слота",
    )
    nearest_slot_start: str | None = None
    nearest_slot_end: str | None = None
    nearest_slot_label: str | None = None


class MeetingAgentSlotPreviewRead(BaseModel):
    memo_ref_key: str
    slot: MeetingSlotRead | None = None
    slot_label: str | None = None
    duration_minutes: int | None = None
    attendees: list[MeetingAttendeeRead] = Field(default_factory=list)
    missing_emails: list[str] = Field(default_factory=list)
    coverage: MeetingSlotCoverageRead | None = None
    conflicts: list[MeetingSlotConflictRead] = Field(default_factory=list)
    slot_candidates: list[MeetingQuorumSlotRead] = Field(default_factory=list)
    search_mode: Literal["all", "partial"] = "all"
    preview_note: str | None = Field(
        default=None,
        description="Пояснение для UI (например, когда нужен разбор УД с переносами)",
    )
    error: str | None = None
    error_stage: str | None = Field(
        default=None,
        description="onec | participants | email | calendar | no_slot — этап сбоя для UI",
    )


class MeetingAgentSlotApproveRequest(BaseModel):
    slot_start: str
    slot_end: str
    attendees: list[MeetingAttendeeRead] | None = None
    attendee_emails: list[str] | None = None
    subject: str | None = None
    location: str | None = None

    @model_validator(mode="after")
    def require_recipients(self) -> MeetingAgentSlotApproveRequest:
        if not self.attendees and not self.attendee_emails:
            raise ValueError(
                "Укажите attendees из ответа slot-preview или список attendee_emails"
            )
        return self


class MeetingAgentSlotApproveRead(BaseModel):
    memo_ref_key: str
    subject: str
    start: str
    end: str
    slot_label: str | None = None
    location: str | None = None
    attendees: list[str] = Field(default_factory=list)
    attendee_details: list[MeetingAttendeeRead] = Field(default_factory=list)
    sent: bool = True
    outlook_item_id: str | None = None
    outlook_changekey: str | None = None
    outlook_meeting_url: str | None = None


class MeetingMemoRejectRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000, description="Причина отклонения")
    notify_initiator: bool = Field(
        default=True,
        description="Отправить уведомление на рабочий стол 1С инициатору СЗ",
    )


class MeetingMemoRejectRead(BaseModel):
    ref_key: str
    number: str | None = None
    status: str | None = None
    previous_status: str | None = None
    reason: str
    comment: str | None = None
    changed: bool = False
    already_rejected: bool = False
    notification_sent: bool = False
    initiator_fio: str | None = None
    rejector_fio: str | None = None
    message: str | None = None


class MeetingMemoApproveRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000, description="Комментарий к согласованию")


class MeetingMemoApproveRead(BaseModel):
    ref_key: str
    number: str | None = None
    status: str | None = None
    previous_status: str | None = None
    changed: bool = False
    already_approved: bool = False
    sto_ready: bool = False
    sto_issues: list[dict[str, str]] = Field(default_factory=list)
    ud_recommendation: str | None = None
    approver_fio: str | None = None
    comment: str | None = None
    message: str | None = None


class MeetingRoomsRequest(BaseModel):
    slot_start: str
    slot_end: str | None = None
    room_name: str | None = None
    duration_minutes: int | None = None

    @field_validator("duration_minutes", mode="before")
    @classmethod
    def normalize_duration(cls, value: object) -> int | None:
        return normalize_request_duration_minutes(value)


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
    memo_ref_key: uuid.UUID | None = None


class MeetingRegistryStageRead(str, Enum):
    INVITATIONS_SENT = "invitations_sent"
    PROTOCOL_CREATED = "protocol_created"
    PROTOCOL_CONDUCTED = "protocol_conducted"
    MEETING_COMPLETED = "meeting_completed"


class MeetingRegistryItemRead(BaseModel):
    ref_key: str
    memo_number: str | None = None
    title: str | None = None
    subject: str | None = None
    location: str | None = None
    initiator_name: str | None = None
    manager_name: str | None = None
    participants_count: int = 0
    slot_start: str | None = None
    slot_end: str | None = None
    stage: MeetingRegistryStageRead
    invitations_sent_at: str
    approved_at: str | None = None
    protocol_number: str | None = None
    outlook_item_id: str | None = None
    outlook_changekey: str | None = None
    outlook_meeting_url: str | None = None
    updated_at: str


class MeetingRegistryRead(BaseModel):
    items: list[MeetingRegistryItemRead] = Field(default_factory=list)
    stage_counts: dict[str, int] = Field(default_factory=dict)
    fetched_at: str
    error: str | None = None
