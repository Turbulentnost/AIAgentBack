from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

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
    timestamp: str = Field(validation_alias=AliasChoices("timestamp", "at"))
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
    event_start_iso: str | None = Field(
        default=None,
        description="Начало конфликта (ISO) для переноса в Outlook",
    )
    event_end_iso: str | None = Field(
        default=None,
        description="Окончание конфликта (ISO) для переноса в Outlook",
    )
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
    planned_start: str | None = Field(
        default=None,
        description="Переопределение начала поиска слота (YYYY-MM-DD HH:MM)",
    )
    search_start: str | None = Field(
        default=None,
        description="Переопределение точки персонального поиска участников",
    )

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
    availability_cache_id: str | None = Field(
        default=None,
        description="Снимок занятости из slot-preview; избегает повторного freebusy при ручной проверке",
    )
    company_calendar_cache_id: str | None = Field(
        default=None,
        description="Кэш совещаний calendar@ на выбранный слот (повторная ручная проверка)",
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


class MeetingSlotRescheduleRecommendationRead(BaseModel):
    participant_fio: str
    event_label: str
    event_time_label: str | None = None
    reschedule_hint_label: str | None = Field(
        default=None,
        description="Предлагаемое окно для переноса конфликтующей встречи",
    )


class MeetingAgentSlotDetailRead(BaseModel):
    memo_ref_key: str
    slot_start: str
    slot_end: str
    slot_label: str
    duration_minutes: int
    participants: list[MeetingSlotParticipantStatusRead] = Field(default_factory=list)
    room: MeetingSlotRoomStatusRead | None = None
    slot_available: bool | None = Field(
        default=None,
        description="Все участники и переговорная свободны в выбранном слоте",
    )
    can_confirm: bool | None = Field(
        default=None,
        description="Можно нажать «Согласовать и утвердить» (слот свободен или есть альтернативы переноса)",
    )
    requires_reschedule: bool = Field(
        default=False,
        description="При утверждении конфликтующие встречи будут перенесены в альтернативные слоты",
    )
    reschedule_recommendations: list[MeetingSlotRescheduleRecommendationRead] = Field(
        default_factory=list,
        description="Встречи, которые нужно перенести для освобождения слота",
    )
    company_calendar_cache_id: str | None = Field(
        default=None,
        description="ID кэша совещаний calendar@ на выбранный слот (TTL 10 мин)",
    )
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
    availability_cache_id: str | None = Field(
        default=None,
        description="ID снимка занятости из подбора слота для быстрой ручной проверки",
    )
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
    participants: list[MeetingSlotParticipantStatusRead] | None = Field(
        default=None,
        description="Статусы из slot-preview/details — для переноса конфликтов перед приглашением",
    )
    company_calendar_cache_id: str | None = Field(
        default=None,
        description="Кэш calendar@ из slot-preview/details (ускоряет повторную проверку слота)",
    )
    subject: str | None = None
    location: str | None = None
    reschedule_message: str | None = Field(
        default=None,
        description="Комментарий в уведомлении о переносе конфликтующих встреч",
    )
    meeting_topic: dict[str, Any] | None = Field(
        default=None,
        description="Выбранная тема совещания из workflow check/resolve",
    )

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
    rescheduled_events: list[str] = Field(
        default_factory=list,
        description="Темы встреч, перенесённых перед отправкой приглашения",
    )


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
    SCHEDULED = "scheduled"
    INVITATIONS_SENT = "invitations_sent"
    PROTOCOL_CREATED = "protocol_created"
    PROTOCOL_CONDUCTED = "protocol_conducted"
    MEETING_COMPLETED = "meeting_completed"
    CANCELLED = "cancelled"


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
    protocol_draft_at: str | None = None
    protocol_draft_created_at: str | None = None
    protocol_draft_error: str | None = None
    protocol_status: str | None = Field(
        default=None,
        description="Последний известный статус протокола в 1С",
    )
    can_cancel: bool = Field(
        default=True,
        description="Можно ли отменить совещание на текущем этапе",
    )
    actions_locked: bool = Field(
        default=False,
        description="Заблокированы ли действия по карточке (завершено / отменено)",
    )
    outlook_item_id: str | None = None
    outlook_changekey: str | None = None
    outlook_meeting_url: str | None = None
    cancelled_at: str | None = None
    updated_at: str
    is_scheduled_series: bool = Field(
        default=False,
        description="Карточка создана из серии запланированных совещаний",
    )
    scheduled_meeting_id: str | None = Field(
        default=None,
        description="ID серии для перехода к карточке серии",
    )
    scheduled_series_badge: str | None = Field(
        default=None,
        description="Текст бейджа на UI, например «Серия»",
    )
    scheduled_series_recurrence_label: str | None = Field(
        default=None,
        description="Периодичность серии для подписи бейджа, например «ежедневно, 9:00»",
    )


class MeetingRegistryCancelRequest(BaseModel):
    message: str = Field(default="", max_length=2000)


class MeetingRegistryCancelRead(BaseModel):
    ref_key: str
    stage: MeetingRegistryStageRead
    cancelled: bool = True
    outlook_cancelled: bool = False
    outlook_warning: str | None = None
    message: str | None = None
    cancelled_at: str | None = None


class MeetingRegistryRead(BaseModel):
    items: list[MeetingRegistryItemRead] = Field(default_factory=list)
    stage_counts: dict[str, int] = Field(default_factory=dict)
    fetched_at: str
    error: str | None = None


class MeetingRegistryProtocolDraftDispatchRead(BaseModel):
    scheduled: int = 0
    catchup_created: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class MeetingRegistryMeetingTopicSaveRead(BaseModel):
    ref_key: str
    topic_ref_key: str | None = None
    topic_code: str | None = None
    topic_description: str | None = None
    meeting_type: str | None = None
    protocol_draft_at: str | None = None


class MeetingRegistryProtocolCreateRead(BaseModel):
    ref_key: str
    created: bool = False
    skipped: bool = False
    reason: str | None = None
    message: str | None = None
    protocol_ref_key: str | None = None
    protocol_number: str | None = None
    stage: MeetingRegistryStageRead | None = None
    protocol_draft_created_at: str | None = None


class MeetingRegistryParticipantsRead(BaseModel):
    ref_key: str
    participants: list[str] = Field(default_factory=list)
    participants_count: int = 0
    pending_confirmation: bool = False
    pending_removed: list[str] = Field(default_factory=list)
    pending_added: list[str] = Field(default_factory=list)
    pending_participants: list[str] | None = Field(
        default=None,
        description="Целевой состав после подтверждения (пока не применён в БД)",
    )
    confirmation_kind: str | None = Field(
        default=None,
        description="removal | add_current_slot | add_reschedule",
    )
    fetched_at: str


class MeetingRegistryParticipantSuggestionRead(BaseModel):
    fio: str
    email: str
    already_added: bool = False


class MeetingRegistryParticipantSearchRead(BaseModel):
    query: str
    fio: str
    email: str | None = None
    found: bool = False
    already_added: bool = False
    can_add: bool = Field(
        default=False,
        description="Можно добавить участника: найден в Outlook и ещё не в списке",
    )
    suggestions: list[MeetingRegistryParticipantSuggestionRead] = Field(
        default_factory=list,
        description="Подсказки при частичном совпадении ФИО",
    )
    message: str | None = Field(
        default=None,
        description="Подсказка для UI: не найден, выберите из списка, уже добавлен",
    )


class MeetingRegistryEventTypeRead(str, Enum):
    INVITATIONS_SENT = "invitations_sent"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    PARTICIPANTS_UPDATED = "participants_updated"
    STAGE_CHANGED = "stage_changed"
    OCCURRENCE_ROLLED = "occurrence_rolled"


class MeetingRegistryEventRead(BaseModel):
    id: str
    event_type: MeetingRegistryEventTypeRead
    occurred_at: str
    message: str
    actor_user_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class MeetingRegistryHistoryRead(BaseModel):
    ref_key: str
    events: list[MeetingRegistryEventRead] = Field(default_factory=list)
    fetched_at: str


class MeetingRegistryParticipantsApplyRequest(BaseModel):
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)
    message: str = Field(default="", max_length=2000)

    @field_validator("added", "removed", "participants", mode="before")
    @classmethod
    def normalize_names(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item or "").strip()]


class MeetingRegistryEarlierSlotCandidateRead(BaseModel):
    slot_start: str
    slot_end: str
    slot_label: str
    coverage_ratio: float | None = None
    free_attendees_count: int | None = None
    total_attendees_count: int | None = None


class MeetingRegistryCurrentSlotAvailabilityRead(BaseModel):
    slot_label: str
    free_count: int
    total_count: int
    all_free: bool
    participants: list[MeetingSlotParticipantStatusRead] = Field(default_factory=list)


class MeetingRegistryEarlierSlotSuggestionRead(BaseModel):
    message: str
    current_slot_label: str
    search_from: str
    search_until: str
    candidates: list[MeetingRegistryEarlierSlotCandidateRead] = Field(default_factory=list)


class MeetingRegistryParticipantsApplyRead(BaseModel):
    ref_key: str
    participants: list[str] = Field(default_factory=list)
    participants_count: int = 0
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    outlook_updated: bool = False
    outlook_warning: str | None = None
    message: str | None = None
    earlier_slot_suggestion: MeetingRegistryEarlierSlotSuggestionRead | None = None
    common_slot_suggestion: MeetingRegistryEarlierSlotSuggestionRead | None = None
    current_slot_availability: MeetingRegistryCurrentSlotAvailabilityRead | None = None
    reschedule_recommendations: list[MeetingSlotRescheduleRecommendationRead] = Field(
        default_factory=list,
        description="Переносы для новых участников на текущем слоте (п.5)",
    )
    requires_reschedule: bool = Field(
        default=False,
        description="Перед добавлением нужно перенести конфликтующие встречи нового участника",
    )
    confirmation_kind: str | None = Field(
        default=None,
        description="removal | add_current_slot | add_reschedule",
    )
    pending_confirmation: bool = False
    fetched_at: str


class MeetingRegistryParticipantsAddConfirmRequest(BaseModel):
    participants: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    slot_start: str | None = None
    slot_end: str | None = None
    message: str = Field(default="", max_length=2000)

    @field_validator("participants", "added", mode="before")
    @classmethod
    def normalize_names(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item or "").strip()]


class MeetingRegistryParticipantsAddConfirmRead(BaseModel):
    ref_key: str
    participants: list[str] = Field(default_factory=list)
    participants_count: int = 0
    added: list[str] = Field(default_factory=list)
    previous_slot_label: str | None = None
    slot_label: str | None = None
    slot_start: str | None = None
    slot_end: str | None = None
    outlook_updated: bool = False
    message: str | None = None
    fetched_at: str


class MeetingRegistryParticipantsRemovalConfirmRequest(BaseModel):
    participants: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    slot_start: str
    slot_end: str
    message: str = Field(default="", max_length=2000)

    @field_validator("participants", "removed", mode="before")
    @classmethod
    def normalize_names(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item or "").strip()]


class MeetingRegistryParticipantsRemovalConfirmRead(BaseModel):
    ref_key: str
    participants: list[str] = Field(default_factory=list)
    participants_count: int = 0
    removed: list[str] = Field(default_factory=list)
    previous_slot_label: str | None = None
    slot_label: str
    slot_start: str
    slot_end: str
    outlook_updated: bool = False
    message: str | None = None
    fetched_at: str


class MeetingRegistryRescheduleSlotPreviewRequest(BaseModel):
    duration_minutes: int | None = None
    mode: Literal["auto", "manual"] = Field(
        default="auto",
        description="auto — подбор слота (п.1); manual — проверка выбранного слота (п.2)",
    )
    slot_start: str | None = Field(
        default=None,
        description="Начало слота для ручного режима",
    )
    slot_end: str | None = Field(
        default=None,
        description="Конец слота для ручного режима",
    )

    @field_validator("duration_minutes", mode="before")
    @classmethod
    def normalize_duration(cls, value: object) -> int | None:
        return normalize_request_duration_minutes(value)


class MeetingRegistryRescheduleSlotPreviewRead(BaseModel):
    ref_key: str
    stage: MeetingRegistryStageRead
    previous_slot_start: str | None = None
    previous_slot_end: str | None = None
    previous_slot_label: str | None = None
    search_after: str | None = None
    mode: Literal["auto", "manual"] = "auto"
    slot_preview: MeetingAgentSlotPreviewRead | None = None
    slot_detail: MeetingAgentSlotDetailRead | None = None


class MeetingRegistryRescheduleApproveRequest(BaseModel):
    slot_start: str
    slot_end: str
    attendees: list[MeetingAttendeeRead] | None = None
    attendee_emails: list[str] | None = None
    subject: str | None = None
    location: str | None = None
    message: str = Field(default="Совещание перенесено", max_length=2000)

    @model_validator(mode="after")
    def require_recipients(self) -> MeetingRegistryRescheduleApproveRequest:
        if not self.attendees and not self.attendee_emails:
            raise ValueError(
                "Укажите attendees из ответа slot-preview или список attendee_emails"
            )
        return self


class MeetingRegistryRescheduleApproveRead(BaseModel):
    ref_key: str
    stage: MeetingRegistryStageRead
    previous_slot_label: str | None = None
    slot_label: str | None = None
    subject: str
    start: str
    end: str
    location: str | None = None
    attendees: list[str] = Field(default_factory=list)
    rescheduled: bool = True
    outlook_updated: bool = False
    new_invite_sent: bool = False
    message: str | None = None
    outlook_item_id: str | None = None
    outlook_changekey: str | None = None
    outlook_meeting_url: str | None = None
