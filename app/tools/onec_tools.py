from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from app.tools.base import Tool
from app.core.config import settings
from app.tools.onec.create_service_memo import (
    DEFAULT_TASK_DESCRIPTION,
    DEFAULT_THEME,
    create_and_send_service_memo,
)
from app.tools.onec.get_meetings import get_last_meeting_memos
from app.tools.onec.lookup_email_by_fio import dispatch_lookup_emails_by_fio
from app.tools.onec.meeting_topics_registry import query_meeting_topics
from app.tools.onec.get_porucheniya import query_porucheniya
from app.services.tasks_manager_resolver import resolve_porucheniya_manager_fio
from app.tools.onec.send_desktop_notification import send_desktop_notifications
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


class GetMeetingDashboardInput(BaseModel):
    target_date: str | None = Field(
        default=None,
        description="Дата совещаний в формате YYYY-MM-DD (по умолчанию — сегодня)",
    )
    limit: int = Field(
        default=500,
        ge=1,
        le=500,
        description="Максимум документов в каждом списке",
    )


class GetMeetingDashboardOutput(BaseModel):
    date: str
    unapproved: list[dict[str, Any]]
    today: list[dict[str, Any]]
    counts: dict[str, int]


async def get_meeting_dashboard_tool(
    payload: GetMeetingDashboardInput,
    context: ToolContext,
) -> GetMeetingDashboardOutput:
    del context
    from datetime import date as date_type

    from app.services.meeting_dashboard_cache import MeetingDashboardCacheService

    target_date = date_type.fromisoformat(payload.target_date) if payload.target_date else None
    cache = MeetingDashboardCacheService()
    raw, _fetched_at, _from_cache = await cache.get_dashboard(
        target_date=target_date,
    )
    raw["unapproved"] = (raw.get("unapproved") or [])[: payload.limit]
    raw["today"] = (raw.get("today") or [])[: payload.limit]
    raw["counts"] = {
        "unapproved": len(raw["unapproved"]),
        "today": len(raw["today"]),
    }
    return GetMeetingDashboardOutput.model_validate(raw)


class GetMeetingDashboardTool(Tool):
    name = "get_meeting_dashboard"
    description = (
        "Возвращает несогласованные служебные записки по совещаниям и совещания на указанную дату из 1С."
    )
    agent_description = (
        "Инструмент get_meeting_dashboard читает Document_ТД_СлужебнаяЗаписка из 1С по теме "
        "ONEC_MEETING_MEMO_THEME. unapproved — Статус «НеСогласована»; today — совещания на дату "
        "(target_date, по умолчанию сегодня). Нужны ONEC_ODATA_* в .env."
    )
    input_model = GetMeetingDashboardInput
    output_model = GetMeetingDashboardOutput
    required_permissions = ["get_meeting_dashboard"]
    preview_default_params = {"limit": 10}

    async def execute(
        self,
        payload: GetMeetingDashboardInput,
        context: ToolContext,
    ) -> GetMeetingDashboardOutput:
        return await get_meeting_dashboard_tool(payload, context)


register_tool(GetMeetingDashboardTool())


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
    description = "Ищет корпоративный e-mail (@turbo-don.ru) сотрудника по ФИО через Exchange GAL (EWS)."
    agent_description = (
        "Инструмент lookup_email_by_fio находит корпоративный e-mail по ФИО в адресной книге Exchange. "
        "Возвращает только адреса @{corporate_domain}. "
        "Передай fio — список ФИО. Нужны OUTLOOK_EMAIL / OUTLOOK_PASSWORD в .env."
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


class CreateServiceMemoInput(BaseModel):
    recipient_fio: str = Field(description="ФИО получателя (Catalog_Пользователи)")
    text: str = Field(description="Текст служебной записки")
    theme: str = Field(
        default=DEFAULT_THEME,
        description="Тема из Catalog_ТД_ТемыСлужебныхЗаписок",
    )
    task_description: str = Field(
        default=DEFAULT_TASK_DESCRIPTION,
        description="Текст задачи для исполнителя в 1С",
    )


class ServiceMemoRecipient(BaseModel):
    fio: str
    user_ref: str


class ServiceMemoInfo(BaseModel):
    ref_key: str | None = None
    number: str | None = None
    date: str | None = None
    posted: bool | None = None
    status: str | None = None


class ServiceMemoTaskInfo(BaseModel):
    ref_key: str | None = None
    number: str | None = None
    description: str | None = None
    executor_ref: str | None = None


class CreateServiceMemoOutput(BaseModel):
    theme: str
    recipient: ServiceMemoRecipient
    memo: ServiceMemoInfo
    task: ServiceMemoTaskInfo


async def create_service_memo_tool(
    payload: CreateServiceMemoInput,
    context: ToolContext,
) -> CreateServiceMemoOutput:
    del context
    raw = await asyncio.to_thread(
        create_and_send_service_memo,
        recipient_fio=payload.recipient_fio,
        text=payload.text,
        theme=payload.theme,
        task_description=payload.task_description,
    )
    return CreateServiceMemoOutput.model_validate(raw)


class CreateServiceMemoTool(Tool):
    name = "create_service_memo"
    description = (
        "Создаёт служебную записку в 1С:ERP и ставит задачу исполнителю по ФИО."
    )
    agent_description = (
        "Инструмент create_service_memo создаёт Document_ТД_СлужебнаяЗаписка в 1С:ERP "
        "и Task_ЗадачаИсполнителя для указанного сотрудника. "
        "recipient_fio — ФИО из Catalog_Пользователи; text — текст записки; "
        f"theme по умолчанию «{DEFAULT_THEME}»; task_description — текст задачи. "
        "Нужны ONEC_ODATA_* в .env."
    )
    input_model = CreateServiceMemoInput
    output_model = CreateServiceMemoOutput
    required_permissions = ["create_service_memo"]
    preview_default_params = {
        "recipient_fio": "Комарькова Анастасия Эдуардовна",
        "text": "Прошу ознакомиться с информацией.",
        "task_description": DEFAULT_TASK_DESCRIPTION,
    }

    async def execute(
        self,
        payload: CreateServiceMemoInput,
        context: ToolContext,
    ) -> CreateServiceMemoOutput:
        return await create_service_memo_tool(payload, context)


register_tool(CreateServiceMemoTool())


class SendDesktopNotificationInput(BaseModel):
    message: str = Field(description="Текст уведомления на рабочий стол 1С")
    recipients_fio: list[str] | None = Field(
        default=None,
        description="ФИО получателей (Catalog_Пользователи). Если не указано — ONEC_NOTIFICATION_DEFAULT_RECIPIENT_FIOS",
    )
    source_user_fio: str | None = Field(
        default=None,
        description="ФИО отправителя для поля Источник, если source_ref не задан",
    )
    source_ref: str | None = Field(
        default=None,
        description="GUID объекта-источника (например, служебной записки)",
    )
    source_type: str | None = Field(
        default=None,
        description="Тип объекта-источника, например Document_ТД_СлужебнаяЗаписка",
    )
    period_close_date: str | None = Field(
        default=None,
        description="Дата закрытия периода (ISO). Сдвигает ВремяСобытия и СрокНапоминания",
    )
    reminder_time_setting_method: str | None = Field(
        default=None,
        description="Способ установки времени: ВУказанноеВремя или ОтносительноТекущегоВремени",
    )
    reminder_time_interval: int | None = Field(
        default=None,
        ge=1,
        description="Интервал напоминания в секундах",
    )


class DesktopNotificationRecipient(BaseModel):
    recipient_fio: str
    user_ref: str
    event_time: str
    reminder_deadline: str
    reminder_id: str


class SendDesktopNotificationOutput(BaseModel):
    register_entity: str
    message: str
    source_user_fio: str
    source_user_ref: str
    source_ref: str | None = None
    source_type: str | None = None
    sent_count: int
    failed_count: int
    notifications: list[DesktopNotificationRecipient]
    errors: list[dict[str, str]]


async def send_desktop_notification_tool(
    payload: SendDesktopNotificationInput,
    context: ToolContext,
) -> SendDesktopNotificationOutput:
    del context
    raw = await asyncio.to_thread(
        send_desktop_notifications,
        message=payload.message,
        recipients_fio=payload.recipients_fio,
        source_user_fio=payload.source_user_fio,
        source_ref=payload.source_ref,
        source_type=payload.source_type,
        period_close_date=payload.period_close_date,
        reminder_time_setting_method=payload.reminder_time_setting_method,
        reminder_time_interval=payload.reminder_time_interval,
    )
    return SendDesktopNotificationOutput.model_validate(raw)


class SendDesktopNotificationTool(Tool):
    name = "send_desktop_notification"
    description = (
        "Отправляет уведомление на рабочий стол 1С через регистр напоминаний пользователя."
    )
    agent_description = (
        "Инструмент send_desktop_notification создаёт запись в "
        "InformationRegister_НапоминанияПользователя (аналог "
        "ОтправитьУведомлениеНаРабочийСтол). message — текст; recipients_fio — "
        "получатели по ФИО; source_ref/source_type — объект-источник (например СЗ); "
        "period_close_date — дата события/дедлайна. Нужны ONEC_ODATA_* в .env."
    )
    input_model = SendDesktopNotificationInput
    output_model = SendDesktopNotificationOutput
    required_permissions = ["send_desktop_notification"]
    preview_default_params = {
        "message": "Требуется согласование служебной записки.",
        "recipients_fio": ["Комарькова Анастасия Эдуардовна"],
    }

    async def execute(
        self,
        payload: SendDesktopNotificationInput,
        context: ToolContext,
    ) -> SendDesktopNotificationOutput:
        return await send_desktop_notification_tool(payload, context)


register_tool(SendDesktopNotificationTool())


class GetPorucheniyaInput(BaseModel):
    period_start: str | None = Field(
        default=None,
        description="Начало периода (YYYY-MM-DD). По умолчанию — вчера",
    )
    period_end: str | None = Field(
        default=None,
        description="Конец периода (YYYY-MM-DD). По умолчанию — вчера",
    )
    limit: int = Field(
        default=500,
        ge=1,
        le=1000,
        description="Максимум документов на источник (поручения и протоколы)",
    )
    author_fio: str | None = Field(
        default=None,
        description=(
            "ФИО руководителя поручения. По умолчанию определяется автоматически: "
            "для ролей «помощник ПСД» и «Помощник Председателя совета директоров» — "
            "Амураль Игорь Борисович, иначе full_name пользователя."
        ),
    )


class GetPorucheniyaOutput(BaseModel):
    document_entity: str
    tabular_entity: str
    register_entity: str | None = None
    protocol_entity: str | None = None
    period_start: str
    period_end: str
    limit: int
    count: int
    counts: dict[str, int] = Field(default_factory=dict)
    author_fio: str | None = None
    manager_fio_source: str | None = None
    selection_method: str
    porucheniya: list[dict[str, Any]] = Field(default_factory=list)
    protocols: list[dict[str, Any]] = Field(default_factory=list)
    protocol_tasks: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Плоский список задач протоколов (для обратной совместимости)",
    )
    items: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Плоский список всех задач (мероприятия + задачи протоколов) для сводок",
    )


async def get_porucheniya_tool(
    payload: GetPorucheniyaInput,
    context: ToolContext,
) -> GetPorucheniyaOutput:
    manager_fio_source = "explicit"
    author_fio = (payload.author_fio or "").strip() or None
    if author_fio is None:
        if context.user is None:
            raise ValueError(
                "Не указано ФИО руководителя: передайте author_fio "
                "или запускайте инструмент от имени пользователя"
            )
        author_fio, manager_fio_source = await resolve_porucheniya_manager_fio(
            context.db,
            context.user,
        )
    raw = await asyncio.to_thread(
        query_porucheniya,
        period_start=payload.period_start,
        period_end=payload.period_end,
        limit=payload.limit,
        author_fio=author_fio,
    )
    raw["manager_fio_source"] = manager_fio_source
    return GetPorucheniyaOutput.model_validate(raw)


class GetPorucheniyaTool(Tool):
    name = "get_porucheniya"
    description = (
        "Возвращает поручения и задачи протоколов из 1С:ERP за указанный период."
    )
    agent_description = (
        "Поручения: Document_ТД_Поручения за период по дате документа, все мероприятия внутри; "
        "протоколы: Document_ТД_Протокол за период по дате документа, все задачи из "
        "InformationRegister_ТД_ЗадачиПротоколов. "
        "По умолчанию возвращает записи, где Руководитель совпадает с full_name пользователя; "
        "для ролей «помощник ПСД» и «Помощник Председателя совета директоров» — "
        "записи руководителя Амураль Игорь Борисович. "
        "period_start/period_end — период YYYY-MM-DD (по умолчанию вчера); limit — максимум документов на источник. "
        "Нужны ONEC_ODATA_* в .env."
    )
    input_model = GetPorucheniyaInput
    output_model = GetPorucheniyaOutput
    required_permissions = ["get_porucheniya"]
    preview_default_params = {"limit": 5}

    async def execute(
        self,
        payload: GetPorucheniyaInput,
        context: ToolContext,
    ) -> GetPorucheniyaOutput:
        return await get_porucheniya_tool(payload, context)


register_tool(GetPorucheniyaTool())
