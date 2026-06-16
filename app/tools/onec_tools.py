from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from app.tools.base import Tool
from app.core.config import settings
from app.tools.onec.get_meetings import get_last_meeting_memos
from app.tools.onec.lookup_email_by_fio import dispatch_lookup_emails_by_fio
from app.tools.onec.meeting_topics_registry import query_meeting_topics
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
    corporate_email_domain: str = "turbo-don.ru"
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
    description = "Ищет корпоративный e-mail (@turbo-don.ru) сотрудника по ФИО через 1С OData."
    agent_description = (
        "Инструмент lookup_email_by_fio находит корпоративный e-mail по ФИО в 1С:ERP. "
        "Возвращает только адреса @{corporate_domain}. "
        "Передай fio — список ФИО. Источники: регистр CRM, каталог учётных записей, "
        "Catalog_СтроковыеКонтактыВзаимодействий, Exchange GAL (OWA/EWS), CRM_ЕмейлДляСинхронизации, "
        "контакты пользователя. Нужны ONEC_ODATA_* и OUTLOOK_* в .env."
    ).format(corporate_domain=settings.ONEC_CORPORATE_EMAIL_DOMAIN or "turbo-don.ru")
    input_model = LookupEmailByFioInput
    output_model = LookupEmailByFioOutput
    required_permissions = ["lookup_email_by_fio"]
    preview_default_params = {"fio": ["Титова Яна Владимировна"]}

    async def execute(
        self,
        payload: LookupEmailByFioInput,
        context: ToolContext,
    ) -> LookupEmailByFioOutput:
        return await lookup_email_by_fio_tool(payload, context)


register_tool(LookupEmailByFioTool())


class GetMeetingTopicsRegistryInput(BaseModel):
    query: str | None = Field(
        default=None,
        description="Поиск по наименованию темы совещания (substringof по Description)",
    )
    code: str | None = Field(default=None, description="Точный код элемента справочника")
    meeting_type: str | None = Field(
        default=None,
        description="Вид совещания, например «Отчетное»",
    )
    ref_key: str | None = Field(
        default=None,
        description="GUID темы совещания — вернуть одну запись",
    )
    active_only: bool = Field(
        default=True,
        description="Только активные темы (без даты закрытия)",
    )
    limit: int = Field(default=20, ge=1, le=200, description="Максимум записей в ответе")
    expand_related: bool = Field(
        default=True,
        description="Развернуть руководителя, подразделение, кабинет, проект, комитет",
    )


class MeetingTopicRepeat(BaseModel):
    days: int | None = None
    weeks: int | None = None
    months: int | None = None
    years: int | None = None
    count: int | None = None
    weekdays: list[dict[str, Any]] = Field(default_factory=list)
    months_of_year: list[dict[str, Any]] = Field(default_factory=list)


class MeetingTopicKeys(BaseModel):
    project: str | None = None
    manager: str | None = None
    reviewer: str | None = None
    department: str | None = None
    room: str | None = None
    committee: str | None = None
    organization: str | None = None
    basis: str | None = None


class MeetingTopicItem(BaseModel):
    ref_key: str | None = None
    code: str | None = None
    description: str
    meeting_type: str | None = None
    priority: str | None = None
    schedule_defined: bool = False
    start_time: str | None = None
    end_time: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    closed_date: str | None = None
    is_active: bool = True
    is_project_topic: bool = False
    is_management_circle_topic: bool = False
    repeat: MeetingTopicRepeat
    keys: MeetingTopicKeys
    manager: str | None = None
    reviewer: str | None = None
    department: str | None = None
    room: str | None = None
    project: str | None = None
    committee: str | None = None


class GetMeetingTopicsRegistryOutput(BaseModel):
    catalog_entity: str
    count: int
    limit: int
    filters: dict[str, Any]
    selection_method: str
    topics: list[MeetingTopicItem]


async def get_meeting_topics_registry_tool(
    payload: GetMeetingTopicsRegistryInput,
    context: ToolContext,
) -> GetMeetingTopicsRegistryOutput:
    del context
    raw = await asyncio.to_thread(
        query_meeting_topics,
        query=payload.query,
        code=payload.code,
        meeting_type=payload.meeting_type,
        active_only=payload.active_only,
        ref_key=payload.ref_key,
        limit=payload.limit,
        expand_related=payload.expand_related,
    )
    return GetMeetingTopicsRegistryOutput.model_validate(raw)


class GetMeetingTopicsRegistryTool(Tool):
    name = "get_meeting_topics_registry"
    description = (
        "Возвращает записи реестра тем совещаний из справочника Catalog_ТД_ТемыСовещаний 1С:ERP OData."
    )
    agent_description = (
        "Инструмент get_meeting_topics_registry читает реестр тем совещаний 1С "
        "(Catalog_ТД_ТемыСовещаний). query — поиск по названию; code — точный код; "
        "meeting_type — вид совещания; ref_key — одна тема по GUID; "
        "active_only=true — только незакрытые темы; expand_related — ФИО руководителя и "
        "название подразделения. Нужны ONEC_ODATA_* в .env."
    )
    input_model = GetMeetingTopicsRegistryInput
    output_model = GetMeetingTopicsRegistryOutput
    required_permissions = ["get_meeting_topics_registry"]
    preview_default_params = {"query": "совещ", "limit": 3, "expand_related": True}

    async def execute(
        self,
        payload: GetMeetingTopicsRegistryInput,
        context: ToolContext,
    ) -> GetMeetingTopicsRegistryOutput:
        return await get_meeting_topics_registry_tool(payload, context)


register_tool(GetMeetingTopicsRegistryTool())
