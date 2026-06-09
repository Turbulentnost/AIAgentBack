from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from app.tools.base import Tool
from app.tools.Outlook.cancel_meeting import dispatch_cancel_meeting
from app.tools.Outlook.read_calendars import fetch_outlook_calendars
from app.tools.Outlook.reschedule_meeting import dispatch_reschedule_meeting
from app.tools.Outlook.send_meeting_invite import dispatch_meeting_invite
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
        "(и changekey) или subject + start. dry_run=true — только проверка без отмены. "
        "message — комментарий участникам. attendee и tolerance_minutes помогают "
        "различить совпадения."
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
        "item_id (с changekey) или subject + start. new_end или duration_minutes "
        "задают новую длительность. location меняет место. dry_run=true — "
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
