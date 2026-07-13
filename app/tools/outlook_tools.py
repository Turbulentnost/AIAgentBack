from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.tools.base import Tool
from app.tools.Outlook.cancel_meeting import dispatch_cancel_meeting
from app.tools.Outlook.find_meeting_slot import (
    dispatch_find_meeting_slot,
    dispatch_find_quorum_meeting_slots,
)
from app.tools.Outlook.meeting_rooms import dispatch_meeting_rooms
from app.tools.Outlook.read_calendars import fetch_outlook_calendars
from app.tools.Outlook.reschedule_meeting import dispatch_reschedule_meeting
from app.tools.Outlook.send_meeting_invite import dispatch_meeting_invite
from app.tools.Outlook.send_recurring_meeting_invite import dispatch_recurring_meeting_invite
from app.tools.Outlook.update_meeting_attendees import dispatch_update_meeting_attendees
from app.tools.registry import register_tool
from app.tools.schemas import ToolContext


class ReadOutlookCalendarsInput(BaseModel):
    list_own: bool = Field(
        default=False,
        description="Показать календари в mailbox Postagent (если EWS видит ящик)",
    )
    owners: list[str] = Field(
        default_factory=list,
        description="SMTP владельцев расшаренных календарей",
    )
    days: int = Field(
        default=14,
        ge=1,
        le=365,
        description="Сколько дней вперёд читать события",
    )
    max_items: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Максимум событий на один календарь",
    )


class ReadOutlookCalendarsOutput(BaseModel):
    login: str
    generated_at: str
    calendars: dict[str, Any] | None = None
    shared_calendars: list[dict[str, Any]] | None = None


async def read_outlook_calendars(
    payload: ReadOutlookCalendarsInput,
    context: ToolContext,
) -> ReadOutlookCalendarsOutput:
    del context
    raw = await asyncio.to_thread(
        fetch_outlook_calendars,
        list_own=payload.list_own,
        owners=payload.owners,
        days=payload.days,
        max_items=payload.max_items,
    )
    return ReadOutlookCalendarsOutput.model_validate(raw)


class ReadOutlookCalendarsTool(Tool):
    name = "read_outlook_calendars"
    description = (
        "Читает календари и встречи через Exchange Web Services (EWS) "
        "под учёткой Postagent из .env."
    )
    agent_description = (
        "Инструмент read_outlook_calendars читает события календаря через EWS. "
        "list_own=true — календари в mailbox Postagent; owners — email владельцев "
        "расшаренных календарей (можно несколько). "
        "days — горизонт в днях, max_items — лимит событий на календарь. "
        "Нужны OUTLOOK_EMAIL, OUTLOOK_PASSWORD, OUTLOOK_SERVER в .env и права "
        "Reviewer/Delegate на чужие календари."
    )
    input_model = ReadOutlookCalendarsInput
    output_model = ReadOutlookCalendarsOutput
    required_permissions = ["read_outlook_calendars"]
    preview_default_params = {"list_own": True, "days": 7, "max_items": 20}

    async def execute(
        self,
        payload: ReadOutlookCalendarsInput,
        context: ToolContext,
    ) -> ReadOutlookCalendarsOutput:
        return await read_outlook_calendars(payload, context)


register_tool(ReadOutlookCalendarsTool())


class SendMeetingInviteInput(BaseModel):
    attendee: str = Field(description="E-mail основного приглашаемого")
    subject: str = Field(description="Тема совещания")
    start: str = Field(
        description="Начало: YYYY-MM-DD HH:MM, YYYY-MM-DDTHH:MM или DD.MM.YYYY HH:MM",
    )
    duration_minutes: int = Field(default=60, ge=1, le=24 * 60)
    body: str = Field(default="", description="Текст приглашения")
    location: str = Field(default="", description="Место проведения")
    attendees: list[str] = Field(
        default_factory=list,
        description="Дополнительные участники (attendee всегда включается в список)",
    )
    resources: list[str] = Field(
        default_factory=list,
        description="E-mail переговорных (ресурсы Exchange)",
    )
    timezone: str | None = Field(
        default=None,
        description="Часовой пояс для start (по умолчанию OUTLOOK_TIMEZONE)",
    )


class SendMeetingInviteOutput(BaseModel):
    status: str
    from_address: str = Field(alias="from")
    login: str
    attendees: list[str]
    subject: str
    start: str
    end: str
    duration_minutes: int
    location: str
    resources: list[str]
    timezone: str

    model_config = {"populate_by_name": True}


async def send_meeting_invite_tool(
    payload: SendMeetingInviteInput,
    context: ToolContext,
) -> SendMeetingInviteOutput:
    del context
    extra = [person.strip() for person in payload.attendees if person.strip()]
    people = [payload.attendee.strip(), *extra]
    unique_people: list[str] = []
    seen: set[str] = set()
    for person in people:
        key = person.lower()
        if person and key not in seen:
            seen.add(key)
            unique_people.append(person)

    raw = await asyncio.to_thread(
        dispatch_meeting_invite,
        attendee=unique_people[0],
        attendees=unique_people,
        subject=payload.subject,
        start=payload.start,
        duration_minutes=payload.duration_minutes,
        body=payload.body,
        location=payload.location,
        resources=payload.resources,
        timezone=payload.timezone,
    )
    return SendMeetingInviteOutput.model_validate(raw)


class SendMeetingInviteTool(Tool):
    name = "send_meeting_invite"
    description = (
        "Отправляет приглашение на совещание через Exchange (EWS) "
        "от имени учётки Postagent из .env."
    )
    agent_description = (
        "Инструмент send_meeting_invite создаёт встречу в календаре Postagent и "
        "рассылает приглашения участникам. Укажи attendee, subject, start "
        "(локальное время), duration_minutes, location, resources (переговорные). "
        "attendees — дополнительные участники. Нужны OUTLOOK_EMAIL, OUTLOOK_PASSWORD, "
        "OUTLOOK_SERVER и доступный mailbox с правом отправки приглашений."
    )
    input_model = SendMeetingInviteInput
    output_model = SendMeetingInviteOutput
    required_permissions = ["send_meeting_invite"]
    preview_default_params = {
        "attendee": "user@example.com",
        "subject": "Совещание",
        "start": "2026-06-05 14:00",
        "duration_minutes": 60,
    }

    async def execute(
        self,
        payload: SendMeetingInviteInput,
        context: ToolContext,
    ) -> SendMeetingInviteOutput:
        return await send_meeting_invite_tool(payload, context)


register_tool(SendMeetingInviteTool())


class SendRecurringMeetingInviteInput(BaseModel):
    attendee: str = Field(description="E-mail основного приглашаемого")
    subject: str = Field(description="Тема совещания")
    start: str = Field(
        description="Первое совещание: YYYY-MM-DD HH:MM, YYYY-MM-DDTHH:MM или DD.MM.YYYY HH:MM",
    )
    duration_minutes: int = Field(default=60, ge=1, le=24 * 60)
    pattern: Literal["weekly", "daily", "monthly"] = Field(
        default="weekly",
        description="Паттерн повторения",
    )
    interval: int = Field(
        default=1,
        ge=1,
        le=99,
        description="Интервал: каждые N недель/дней/месяцев",
    )
    weekdays: list[str] = Field(
        default_factory=list,
        description="Дни недели для weekly (Monday..Sunday). Пусто — день из start",
    )
    day_of_month: int | None = Field(
        default=None,
        ge=1,
        le=31,
        description="День месяца для monthly",
    )
    end_type: Literal["occurrences", "end_date", "no_end"] = Field(
        default="occurrences",
        description="Как заканчивается серия",
    )
    occurrences: int | None = Field(
        default=3,
        ge=1,
        le=999,
        description="Число встреч при end_type=occurrences",
    )
    end: str | None = Field(
        default=None,
        description="Дата окончания серии при end_type=end_date (YYYY-MM-DD)",
    )
    body: str = Field(default="", description="Текст приглашения")
    location: str = Field(default="", description="Место проведения")
    attendees: list[str] = Field(
        default_factory=list,
        description="Дополнительные участники",
    )
    resources: list[str] = Field(
        default_factory=list,
        description="E-mail переговорных",
    )
    timezone: str | None = Field(
        default=None,
        description="Часовой пояс для start (по умолчанию OUTLOOK_TIMEZONE)",
    )


class SendRecurringMeetingInviteOutput(BaseModel):
    status: str
    from_address: str = Field(alias="from")
    login: str
    attendees: list[str]
    subject: str
    start: str
    end: str
    duration_minutes: int
    location: str
    resources: list[str]
    timezone: str
    recurrence: str
    recurrence_summary: dict[str, Any]
    warning: str | None = None

    model_config = {"populate_by_name": True}


async def send_recurring_meeting_invite_tool(
    payload: SendRecurringMeetingInviteInput,
    context: ToolContext,
) -> SendRecurringMeetingInviteOutput:
    del context
    extra = [person.strip() for person in payload.attendees if person.strip()]
    people = [payload.attendee.strip(), *extra]
    unique_people: list[str] = []
    seen: set[str] = set()
    for person in people:
        key = person.lower()
        if person and key not in seen:
            seen.add(key)
            unique_people.append(person)

    raw = await asyncio.to_thread(
        dispatch_recurring_meeting_invite,
        attendee=unique_people[0],
        attendees=unique_people,
        subject=payload.subject,
        start=payload.start,
        duration_minutes=payload.duration_minutes,
        pattern=payload.pattern,
        interval=payload.interval,
        weekdays=payload.weekdays or None,
        day_of_month=payload.day_of_month,
        end_type=payload.end_type,
        occurrences=payload.occurrences,
        end=payload.end,
        body=payload.body,
        location=payload.location,
        resources=payload.resources,
        timezone=payload.timezone,
    )
    return SendRecurringMeetingInviteOutput.model_validate(raw)


class SendRecurringMeetingInviteTool(Tool):
    name = "send_recurring_meeting_invite"
    description = (
        "Создаёт повторяющееся совещание в календаре Exchange (EWS) "
        "и рассылает приглашения участникам."
    )
    agent_description = (
        "Инструмент send_recurring_meeting_invite создаёт серию встреч в календаре Postagent. "
        "Укажи attendee, subject, start, duration_minutes, pattern (weekly/daily/monthly). "
        "Для weekly — weekdays (например Tuesday); end_type=occurrences с occurrences=3 "
        "или end_type=end_date с end=YYYY-MM-DD. attendees и resources — как в send_meeting_invite."
    )
    input_model = SendRecurringMeetingInviteInput
    output_model = SendRecurringMeetingInviteOutput
    required_permissions = ["send_meeting_invite"]
    preview_default_params = {
        "attendee": "user@example.com",
        "subject": "Регламентное совещание",
        "start": "2026-07-14 16:00",
        "duration_minutes": 30,
        "pattern": "weekly",
        "weekdays": ["Tuesday"],
        "end_type": "occurrences",
        "occurrences": 3,
    }

    async def execute(
        self,
        payload: SendRecurringMeetingInviteInput,
        context: ToolContext,
    ) -> SendRecurringMeetingInviteOutput:
        return await send_recurring_meeting_invite_tool(payload, context)


register_tool(SendRecurringMeetingInviteTool())


class CancelMeetingInput(BaseModel):
    list_only: bool = Field(
        default=False,
        description="Показать совещания в календаре организатора",
    )
    days: int = Field(default=14, ge=1, le=365)
    item_id: str = Field(default="", description="EWS ItemId совещания")
    changekey: str = Field(default="", description="EWS ChangeKey")
    subject: str = Field(default="", description="Тема для поиска совещания")
    start: str = Field(default="", description="Начало для поиска: YYYY-MM-DD HH:MM")
    attendee: str = Field(default="", description="E-mail участника для уточнения поиска")
    tolerance_minutes: int = Field(default=5, ge=0, le=120)
    message: str = Field(default="", description="Комментарий в уведомлении об отмене")
    cancel_scope: Literal["occurrence", "series"] = Field(
        default="occurrence",
        description="occurrence — одно совещание; series — всю серию",
    )
    dry_run: bool = Field(
        default=False,
        description="Только показать совещение без отмены",
    )
    timezone: str | None = Field(
        default=None,
        description="Часовой пояс для start (по умолчанию OUTLOOK_TIMEZONE)",
    )


class CancelMeetingOutput(BaseModel):
    action: str
    status: str | None = None
    calendar: str | None = None
    meetings_count: int | None = None
    meetings: list[dict[str, Any]] | None = None
    meeting: dict[str, Any] | None = None
    message: str | None = None
    cancel_scope: str | None = None
    target_kind: str | None = None
    target_id: str | None = None
    error: str | None = None


async def cancel_meeting_tool(
    payload: CancelMeetingInput,
    context: ToolContext,
) -> CancelMeetingOutput:
    del context
    raw = await asyncio.to_thread(
        dispatch_cancel_meeting,
        list_only=payload.list_only,
        days=payload.days,
        item_id=payload.item_id,
        changekey=payload.changekey,
        subject=payload.subject,
        start=payload.start,
        attendee=payload.attendee,
        tolerance_minutes=payload.tolerance_minutes,
        message=payload.message,
        dry_run=payload.dry_run,
        cancel_scope=payload.cancel_scope,
        timezone=payload.timezone,
    )
    return CancelMeetingOutput.model_validate(raw)


class CancelMeetingTool(Tool):
    name = "cancel_meeting"
    description = (
        "Отменяет совещание в календаре Exchange (EWS) и рассылает уведомление участникам."
    )
    agent_description = (
        "Инструмент cancel_meeting отменяет встречу в календаре Postagent. "
        "list_only=true — список совещаний с id/changekey; для отмены укажи item_id "
        "(и changekey) или subject + start. cancel_scope=occurrence отменяет одно "
        "совещание из серии; cancel_scope=series — всю серию. dry_run=true — только "
        "проверка без отмены. message — комментарий участникам."
    )
    input_model = CancelMeetingInput
    output_model = CancelMeetingOutput
    required_permissions = ["cancel_meeting"]
    preview_default_params = {"list_only": True, "days": 7}

    async def execute(
        self,
        payload: CancelMeetingInput,
        context: ToolContext,
    ) -> CancelMeetingOutput:
        return await cancel_meeting_tool(payload, context)


register_tool(CancelMeetingTool())


class RescheduleMeetingInput(BaseModel):
    list_only: bool = Field(
        default=False,
        description="Показать совещания в календаре организатора",
    )
    days: int = Field(default=14, ge=1, le=365)
    item_id: str = Field(default="", description="EWS ItemId совещания")
    changekey: str = Field(default="", description="EWS ChangeKey")
    subject: str = Field(default="", description="Тема для поиска совещания")
    start: str = Field(default="", description="Текущее начало для поиска")
    new_start: str = Field(default="", description="Новое начало совещания")
    new_end: str = Field(
        default="",
        description="Новый конец (иначе сохраняется длительность или duration_minutes)",
    )
    duration_minutes: int | None = Field(
        default=None,
        ge=1,
        le=24 * 60,
        description="Новая длительность в минутах",
    )
    location: str | None = Field(
        default=None,
        description="Новое место (если не указано — не меняется)",
    )
    attendee: str = Field(default="", description="E-mail участника для уточнения поиска")
    tolerance_minutes: int = Field(default=5, ge=0, le=120)
    message: str = Field(default="", description="Комментарий в уведомлении о переносе")
    reschedule_scope: Literal["occurrence", "series"] = Field(
        default="occurrence",
        description="occurrence — одно совещание; series — всю серию",
    )
    dry_run: bool = Field(default=False, description="Только показать изменения без переноса")
    timezone: str | None = Field(
        default=None,
        description="Часовой пояс для start/new_start (по умолчанию OUTLOOK_TIMEZONE)",
    )


class RescheduleMeetingOutput(BaseModel):
    action: str
    status: str | None = None
    calendar: str | None = None
    meetings_count: int | None = None
    meetings: list[dict[str, Any]] | None = None
    meeting: dict[str, Any] | None = None
    new_start: str | None = None
    new_end: str | None = None
    location: str | None = None
    message: str | None = None
    reschedule_scope: str | None = None
    target_kind: str | None = None
    target_id: str | None = None
    error: str | None = None


async def reschedule_meeting_tool(
    payload: RescheduleMeetingInput,
    context: ToolContext,
) -> RescheduleMeetingOutput:
    del context
    raw = await asyncio.to_thread(
        dispatch_reschedule_meeting,
        list_only=payload.list_only,
        days=payload.days,
        item_id=payload.item_id,
        changekey=payload.changekey,
        subject=payload.subject,
        start=payload.start,
        new_start=payload.new_start,
        new_end=payload.new_end,
        duration_minutes=payload.duration_minutes,
        location=payload.location,
        attendee=payload.attendee,
        tolerance_minutes=payload.tolerance_minutes,
        message=payload.message,
        dry_run=payload.dry_run,
        reschedule_scope=payload.reschedule_scope,
        timezone=payload.timezone,
    )
    return RescheduleMeetingOutput.model_validate(raw)


class RescheduleMeetingTool(Tool):
    name = "reschedule_meeting"
    description = (
        "Переносит совещание в календаре Exchange (EWS) и рассылает обновлённое приглашение."
    )
    agent_description = (
        "Инструмент reschedule_meeting переносит встречу на новое время. "
        "list_only=true — список совещаний; для переноса укажи new_start и "
        "item_id (с changekey) или subject + start. reschedule_scope=occurrence "
        "переносит одно совещание из серии; reschedule_scope=series — всю серию. "
        "new_end или duration_minutes задают новую длительность. dry_run=true — "
        "только проверка. message — комментарий участникам."
    )
    input_model = RescheduleMeetingInput
    output_model = RescheduleMeetingOutput
    required_permissions = ["reschedule_meeting"]
    preview_default_params = {"list_only": True, "days": 7}

    async def execute(
        self,
        payload: RescheduleMeetingInput,
        context: ToolContext,
    ) -> RescheduleMeetingOutput:
        return await reschedule_meeting_tool(payload, context)


register_tool(RescheduleMeetingTool())


class UpdateMeetingAttendeesInput(BaseModel):
    list_only: bool = Field(default=False, description="Показать совещания в календаре организатора")
    days: int = Field(default=14, ge=1, le=365)
    item_id: str = Field(default="", description="EWS ItemId совещания")
    changekey: str = Field(default="", description="EWS ChangeKey")
    subject: str = Field(default="", description="Тема для поиска совещания")
    start: str = Field(default="", description="Начало для поиска: YYYY-MM-DD HH:MM")
    attendee: str = Field(default="", description="E-mail участника для уточнения поиска")
    tolerance_minutes: int = Field(default=5, ge=0, le=120)
    add: list[str] = Field(default_factory=list, description="E-mail участников для добавления")
    remove: list[str] = Field(default_factory=list, description="E-mail участников для удаления")
    attendees_scope: Literal["occurrence", "series"] = Field(
        default="occurrence",
        description="occurrence — одно совещание; series — всю серию",
    )
    message: str = Field(default="", description="Комментарий в уведомлении")
    dry_run: bool = Field(default=False, description="Только показать изменения")
    timezone: str | None = Field(default=None, description="Часовой пояс для start")


class UpdateMeetingAttendeesOutput(BaseModel):
    action: str
    status: str | None = None
    calendar: str | None = None
    meetings_count: int | None = None
    meetings: list[dict[str, Any]] | None = None
    meeting: dict[str, Any] | None = None
    add: list[str] | None = None
    remove: list[str] | None = None
    before: list[str] | None = None
    after: list[str] | None = None
    added: list[str] | None = None
    removed: list[str] | None = None
    skipped_remove: list[str] | None = None
    notified_existing: list[str] | None = None
    notified_new: list[str] | None = None
    notified_removed: list[str] | None = None
    notification_errors: list[str] | None = None
    attendees_scope: str | None = None
    target_kind: str | None = None
    target_id: str | None = None
    message: str | None = None
    error: str | None = None


async def update_meeting_attendees_tool(
    payload: UpdateMeetingAttendeesInput,
    context: ToolContext,
) -> UpdateMeetingAttendeesOutput:
    del context
    raw = await asyncio.to_thread(
        dispatch_update_meeting_attendees,
        list_only=payload.list_only,
        days=payload.days,
        item_id=payload.item_id,
        changekey=payload.changekey,
        subject=payload.subject,
        start=payload.start,
        attendee=payload.attendee,
        tolerance_minutes=payload.tolerance_minutes,
        add=payload.add,
        remove=payload.remove,
        message=payload.message,
        dry_run=payload.dry_run,
        attendees_scope=payload.attendees_scope,
        timezone=payload.timezone,
    )
    return UpdateMeetingAttendeesOutput.model_validate(raw)


class UpdateMeetingAttendeesTool(Tool):
    name = "update_meeting_attendees"
    description = (
        "Добавляет или удаляет участников совещания в календаре Exchange (EWS) "
        "и рассылает персональные уведомления: существующим — об обновлении состава, "
        "новым — календарное приглашение, удалённым — уведомление об исключении."
    )
    agent_description = (
        "Инструмент update_meeting_attendees меняет состав участников встречи. "
        "Укажи add/remove и item_id или subject+start. attendees_scope=occurrence "
        "меняет одно вхождение серии, attendees_scope=series — всю серию. "
        "Существующим — письмо об обновлении состава, новым — календарное приглашение, "
        "удалённым — уведомление об исключении. dry_run=true — только предпросмотр."
    )
    input_model = UpdateMeetingAttendeesInput
    output_model = UpdateMeetingAttendeesOutput
    required_permissions = ["reschedule_meeting"]
    preview_default_params = {"list_only": True, "days": 7}

    async def execute(
        self,
        payload: UpdateMeetingAttendeesInput,
        context: ToolContext,
    ) -> UpdateMeetingAttendeesOutput:
        return await update_meeting_attendees_tool(payload, context)


register_tool(UpdateMeetingAttendeesTool())


class FindMeetingSlotInput(BaseModel):
    attendees: list[str] = Field(description="E-mail участников совещания")
    preferred: str = Field(description="Желаемая дата/время начала поиска")
    duration_minutes: int = Field(ge=1, le=24 * 60, description="Длительность в минутах")
    max_days: int = Field(default=30, ge=1, le=365)
    step_minutes: int = Field(default=15, ge=1, le=120)
    max_items: int = Field(default=500, ge=1, le=2000)
    source: Literal["freebusy", "calendar"] = Field(
        default="freebusy",
        description="freebusy — быстро; calendar — запасной вариант",
    )
    workers: int = Field(default=4, ge=1, le=16)
    timezone: str | None = Field(
        default=None,
        description="Часовой пояс для preferred (по умолчанию OUTLOOK_TIMEZONE)",
    )
    rooms_file: str | None = Field(
        default=None,
        description="JSON со списком переговорных",
    )
    skip_rooms: bool = Field(default=False, description="Не проверять переговорные")
    verify_calendar: bool = Field(
        default=True,
        description="Сверять найденный слот с calendar_events из GetUserAvailability (рекомендуется)",
    )
    quiet: bool = Field(
        default=True,
        description="Без подробных логов поиска (False — только при явном запросе пользователя)",
    )
    include_timing: bool = Field(
        default=False,
        description="Печатать тайминги шагов EWS в stderr",
    )


class FindMeetingSlotOutput(BaseModel):
    preferred: str
    slot_start: str
    slot_end: str
    duration_minutes: int
    attendees: list[str]
    checked_candidates: int
    search_until: str
    availability_source: str
    rooms_status: list[dict[str, Any]] | None = None


async def find_meeting_slot_tool(
    payload: FindMeetingSlotInput,
    context: ToolContext,
) -> FindMeetingSlotOutput:
    del context
    raw = await asyncio.to_thread(
        dispatch_find_meeting_slot,
        attendees=payload.attendees,
        preferred=payload.preferred,
        duration_minutes=payload.duration_minutes,
        max_days=payload.max_days,
        step_minutes=payload.step_minutes,
        max_items=payload.max_items,
        source=payload.source,
        workers=payload.workers,
        timezone=payload.timezone,
        rooms_file=payload.rooms_file,
        skip_rooms=payload.skip_rooms,
        verify_calendar=payload.verify_calendar,
        quiet=payload.quiet,
        include_timing=payload.include_timing,
    )
    return FindMeetingSlotOutput.model_validate(raw)


class FindMeetingSlotTool(Tool):
    name = "find_meeting_slot"
    description = (
        "Ищет ближайший свободный слот для совещания у нескольких участников через EWS."
    )
    agent_description = (
        "Инструмент find_meeting_slot подбирает время совещания с учётом занятости "
        "участников: только рабочие дни пн–пт, 08:00–17:00, без пересечений с "
        "обеденным перерывом 12:00–13:00. "
        "Укажи attendees, preferred (желаемое время), duration_minutes. "
        "source=freebusy быстрее; skip_rooms=true отключает проверку переговорных."
    )
    input_model = FindMeetingSlotInput
    output_model = FindMeetingSlotOutput
    required_permissions = ["find_meeting_slot"]
    preview_default_params = {
        "attendees": ["user@example.com"],
        "preferred": "2026-06-10 14:00",
        "duration_minutes": 60,
        "skip_rooms": True,
    }

    async def execute(
        self,
        payload: FindMeetingSlotInput,
        context: ToolContext,
    ) -> FindMeetingSlotOutput:
        return await find_meeting_slot_tool(payload, context)


register_tool(FindMeetingSlotTool())


class MeetingSlotCoverageOutput(BaseModel):
    free: int
    total: int
    ratio: float
    weighted_ratio: float | None = None
    required_ok: bool


class MeetingSlotConflictOutput(BaseModel):
    email: str
    event_start: str | None = None
    event_end: str | None = None
    event_subject: str | None = None
    busy_type: str | None = None
    movability: str = "medium"
    can_auto_reschedule: bool = False
    reschedule_hint_start: str | None = None
    reschedule_hint_end: str | None = None


class MeetingQuorumCandidateOutput(BaseModel):
    slot_start: str
    slot_end: str
    duration_minutes: int
    coverage: MeetingSlotCoverageOutput
    free_attendees: list[str]
    busy_attendees: list[str]
    conflicts: list[MeetingSlotConflictOutput] = Field(default_factory=list)
    verified: bool = False
    confidence: float
    impact_score: float | None = None
    busy_weight_cost: float | None = None
    reschedule_count: int = 0
    easy_reschedule_count: int = 0
    low_movability_count: int = 0


class FindQuorumMeetingSlotsInput(BaseModel):
    attendees: list[str] = Field(description="E-mail всех участников совещания")
    required_attendees: list[str] = Field(
        default_factory=list,
        description="E-mail обязательных участников (инициатор, руководитель, директор). Пусто — по ролям",
    )
    attendee_weights: dict[str, float] = Field(
        default_factory=dict,
        description="Вес e-mail для weighted quorum (инициатор/руководитель/директор = 3, участник = 1)",
    )
    preferred: str = Field(description="Желаемая дата/время начала поиска")
    duration_minutes: int = Field(ge=1, le=24 * 60, description="Длительность в минутах")
    min_coverage_ratio: float = Field(
        default=0.7,
        gt=0,
        le=1,
        description="Минимальная доля свободных участников (0.7 = 70%)",
    )
    max_results: int = Field(default=3, ge=1, le=10, description="Сколько лучших слотов вернуть")
    verify_top_n: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Сколько топ-слотов дополнительно сверить через calendar_events",
    )
    max_days: int = Field(default=30, ge=1, le=365)
    step_minutes: int = Field(default=15, ge=1, le=120)
    max_items: int = Field(default=500, ge=1, le=2000)
    source: Literal["freebusy", "calendar"] = Field(default="freebusy")
    workers: int = Field(default=4, ge=1, le=16)
    timezone: str | None = None
    verify_calendar: bool = Field(
        default=True,
        description="Сверять топ-слоты с calendar_events (verify_top_n штук)",
    )
    quiet: bool = Field(default=True)
    include_timing: bool = Field(default=False)


class FindQuorumMeetingSlotsOutput(BaseModel):
    preferred: str
    earliest_allowed: str
    search_until: str
    min_coverage_ratio: float
    required_attendees: list[str]
    attendees: list[str]
    checked_candidates: int
    availability_source: str
    search_mode: str
    candidates: list[MeetingQuorumCandidateOutput]


async def find_quorum_meeting_slots_tool(
    payload: FindQuorumMeetingSlotsInput,
    context: ToolContext,
) -> FindQuorumMeetingSlotsOutput:
    del context
    raw = await asyncio.to_thread(
        dispatch_find_quorum_meeting_slots,
        attendees=payload.attendees,
        required_attendees=payload.required_attendees or None,
        attendee_weights=payload.attendee_weights or None,
        preferred=payload.preferred,
        duration_minutes=payload.duration_minutes,
        min_coverage_ratio=payload.min_coverage_ratio,
        max_results=payload.max_results,
        verify_top_n=payload.verify_top_n,
        max_days=payload.max_days,
        step_minutes=payload.step_minutes,
        max_items=payload.max_items,
        source=payload.source,
        workers=payload.workers,
        timezone=payload.timezone,
        verify_calendar=payload.verify_calendar,
        quiet=payload.quiet,
        include_timing=payload.include_timing,
    )
    return FindQuorumMeetingSlotsOutput.model_validate(raw)


class FindQuorumMeetingSlotsTool(Tool):
    name = "find_quorum_meeting_slots"
    description = (
        "Ищет слоты совещания для большинства участников с анализом конфликтов "
        "и подсказками по переносу мешающих встреч."
    )
    agent_description = (
        "Инструмент find_quorum_meeting_slots подбирает время, когда свободны "
        "обязательные участники и не менее min_coverage_ratio остальных. "
        "Возвращает топ-слоты, список конфликтов и movability для перепланирования."
    )
    input_model = FindQuorumMeetingSlotsInput
    output_model = FindQuorumMeetingSlotsOutput
    required_permissions = ["find_meeting_slot"]
    preview_default_params = {
        "attendees": ["user@example.com"],
        "required_attendees": ["user@example.com"],
        "preferred": "2026-06-10 14:00",
        "duration_minutes": 60,
    }

    async def execute(
        self,
        payload: FindQuorumMeetingSlotsInput,
        context: ToolContext,
    ) -> FindQuorumMeetingSlotsOutput:
        return await find_quorum_meeting_slots_tool(payload, context)


register_tool(FindQuorumMeetingSlotsTool())


class MeetingRoomsInput(BaseModel):
    list_only: bool = Field(
        default=True,
        description="Вернуть список переговорных",
    )
    check: bool = Field(
        default=False,
        description="Проверить занятость переговорных на слот",
    )
    discover: bool = Field(
        default=False,
        description="Дополнить список комнатами из Exchange",
    )
    rooms_file: str | None = Field(
        default=None,
        description="Путь к JSON со списком переговорных",
    )
    start: str = Field(
        default="",
        description="Начало слота для check: YYYY-MM-DD HH:MM",
    )
    duration_minutes: int = Field(default=60, ge=1, le=24 * 60)
    timezone: str | None = Field(
        default=None,
        description="Часовой пояс для start (по умолчанию OUTLOOK_TIMEZONE)",
    )


class MeetingRoomsOutput(BaseModel):
    rooms: list[dict[str, str]]
    rooms_count: int
    pending_without_email: list[str] = Field(default_factory=list)
    discovered_not_in_json: list[dict[str, str]] = Field(default_factory=list)
    slot_start: str | None = None
    slot_end: str | None = None
    rooms_status: list[dict[str, Any]] | None = None
    free_count: int | None = None


async def meeting_rooms_tool(
    payload: MeetingRoomsInput,
    context: ToolContext,
) -> MeetingRoomsOutput:
    del context
    raw = await asyncio.to_thread(
        dispatch_meeting_rooms,
        list_only=payload.list_only,
        check=payload.check,
        discover=payload.discover,
        rooms_file=payload.rooms_file,
        start=payload.start,
        duration_minutes=payload.duration_minutes,
        timezone=payload.timezone,
    )
    return MeetingRoomsOutput.model_validate(raw)


class MeetingRoomsTool(Tool):
    name = "meeting_rooms"
    description = (
        "Список переговорных Exchange и проверка их занятости через EWS Free/Busy."
    )
    agent_description = (
        "Инструмент meeting_rooms возвращает переговорные комнаты и их занятость. "
        "list_only=true — список из meeting_rooms.json; discover=true дополняет "
        "комнатами из Exchange. check=true с start и duration_minutes проверяет "
        "свободность всех комнат на указанный слот."
    )
    input_model = MeetingRoomsInput
    output_model = MeetingRoomsOutput
    required_permissions = ["meeting_rooms"]
    preview_default_params = {"list_only": True, "discover": False}

    async def execute(
        self,
        payload: MeetingRoomsInput,
        context: ToolContext,
    ) -> MeetingRoomsOutput:
        return await meeting_rooms_tool(payload, context)


register_tool(MeetingRoomsTool())
