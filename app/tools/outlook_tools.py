from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from app.tools.base import Tool
from app.tools.Outlook.read_calendars import fetch_outlook_calendars
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
