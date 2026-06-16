from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from app.tools.base import Tool
from app.tools.onec.get_meetings import get_last_meeting_memos
from app.tools.onec.lookup_email_by_fio import dispatch_lookup_emails_by_fio
from app.tools.registry import register_tool
from app.tools.schemas import ToolContext


class GetMeetingMemosInput(BaseModel):
    limit: int = Field(default=5, ge=1, le=50, description="Сколько последних СЗ вернуть")
    fetch_pool: int = Field(
        default=200,
        ge=1,
        le=1000,
        description="Сколько документов запрашивать у OData для отбора",
    )
    compact: bool = Field(
        default=False,
        description="Не включать полную шапку header (только memo + participants)",
    )


class GetMeetingMemosOutput(BaseModel):
    document_type: str
    theme: str
    limit: int
    count: int
    selection_method: str
    tabular_entities: list[str]
    documents: list[dict[str, Any]]


async def get_meeting_memos_tool(
    payload: GetMeetingMemosInput,
    context: ToolContext,
) -> GetMeetingMemosOutput:
    del context
    raw = await asyncio.to_thread(
        get_last_meeting_memos,
        limit=payload.limit,
        fetch_pool=max(payload.fetch_pool, payload.limit),
        include_full_header=not payload.compact,
    )
    return GetMeetingMemosOutput.model_validate(raw)


class GetMeetingMemosTool(Tool):
    name = "get_meeting_memos"
    description = (
        "Возвращает последние служебные записки «Организация совещаний (регл.)» из 1С:ERP OData."
    )
    agent_description = (
        "Инструмент get_meeting_memos читает Document_ТД_СлужебнаяЗаписка из 1С по теме "
        "ONEC_MEETING_MEMO_THEME. limit — сколько последних документов вернуть; "
        "compact=true — без полной шапки header. Нужны ONEC_ODATA_* в .env."
    )
    input_model = GetMeetingMemosInput
    output_model = GetMeetingMemosOutput
    required_permissions = ["get_meeting_memos"]
    preview_default_params = {"limit": 2, "compact": True}

    async def execute(
        self,
        payload: GetMeetingMemosInput,
        context: ToolContext,
    ) -> GetMeetingMemosOutput:
        return await get_meeting_memos_tool(payload, context)


register_tool(GetMeetingMemosTool())


class LookupEmailByFioInput(BaseModel):
    fio: list[str] = Field(description="ФИО пользователей для поиска e-mail")


class LookupEmailResult(BaseModel):
    fio_query: str
    fio: str
    user_ref: str
    register_published: bool
    emails: list[dict[str, Any]]


class LookupEmailError(BaseModel):
    fio: str
    error: str


class LookupEmailByFioOutput(BaseModel):
    register_entity: str
    register_published: bool
    results: list[LookupEmailResult]
    errors: list[LookupEmailError] = Field(default_factory=list)


async def lookup_email_by_fio_tool(
    payload: LookupEmailByFioInput,
    context: ToolContext,
) -> LookupEmailByFioOutput:
    del context
    raw = await asyncio.to_thread(dispatch_lookup_emails_by_fio, payload.fio)
    return LookupEmailByFioOutput.model_validate(raw)


class LookupEmailByFioTool(Tool):
    name = "lookup_email_by_fio"
    description = "Ищет e-mail сотрудника по ФИО через 1С OData."
    agent_description = (
        "Инструмент lookup_email_by_fio находит e-mail по ФИО в 1С:ERP. "
        "Передай fio — список ФИО. Источники: регистр CRM, каталог учётных записей, "
        "CRM_ЕмейлДляСинхронизации, контактная информация. "
        "Нужны ONEC_ODATA_* в .env."
    )
    input_model = LookupEmailByFioInput
    output_model = LookupEmailByFioOutput
    required_permissions = ["lookup_email_by_fio"]
    preview_default_params = {"fio": ["Кербенева Ольга Владимировна"]}

    async def execute(
        self,
        payload: LookupEmailByFioInput,
        context: ToolContext,
    ) -> LookupEmailByFioOutput:
        return await lookup_email_by_fio_tool(payload, context)


register_tool(LookupEmailByFioTool())
