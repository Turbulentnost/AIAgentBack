from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from app.tools.base import Tool
from app.core.config import settings
from app.tools.onec.approve_service_memo import approve_service_memo
from app.tools.onec.reject_service_memo import reject_service_memo
from app.tools.onec.create_service_memo import (
    DEFAULT_TASK_DESCRIPTION,
    DEFAULT_THEME,
    create_and_send_service_memo,
)
from app.tools.onec.create_protocol import create_meeting_protocol, delete_meeting_protocol
from app.tools.onec.create_meeting_topic import MEETING_TYPES, create_meeting_topic
from app.tools.onec.meeting_topic_participants import get_meeting_topic_participants
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
    items: list[dict[str, Any]] = Field(default_factory=list)
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
    if not raw.get("items"):
        from app.agents.meeting_agent.dashboard import merge_dashboard_items

        raw["items"] = merge_dashboard_items(raw.get("unapproved") or [], raw.get("today") or [])
    raw["unapproved"] = (raw.get("unapproved") or [])[: payload.limit]
    raw["today"] = (raw.get("today") or [])[: payload.limit]
    raw["items"] = (raw.get("items") or [])[: payload.limit]
    raw["counts"] = {
        "unapproved": len(raw["unapproved"]),
        "today": len(raw["today"]),
        "items": len(raw["items"]),
    }
    return GetMeetingDashboardOutput.model_validate(raw)


class GetMeetingDashboardTool(Tool):
    name = "get_meeting_dashboard"
    description = (
        "Возвращает служебные записки по совещаниям из 1С: несогласованные за всё время "
        "и все СЗ за указанную дату (любой статус)."
    )
    agent_description = (
        "Инструмент get_meeting_dashboard читает Document_ТД_СлужебнаяЗаписка из 1С по теме "
        "ONEC_MEETING_MEMO_THEME. unapproved — Статус «НеСогласована» за всё время; "
        "today — все СЗ с датой документа (Date) за target_date, любой статус; "
        "items — объединённый список без дублей. "
        "target_date по умолчанию — сегодня. Нужны ONEC_ODATA_* в .env."
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


class ApproveServiceMemoInput(BaseModel):
    ref_key: str | None = Field(default=None, description="Ref_Key служебной записки")
    number: str | None = Field(default=None, description="Номер служебной записки, например 000010430")
    approver_fio: str | None = Field(
        default=None,
        description="ФИО согласующего (Catalog_Пользователи) для поля ИсполнительУД",
    )
    comment: str | None = Field(default=None, description="Комментарий к согласованию")


class ApproveServiceMemoOutput(BaseModel):
    ref_key: str
    number: str | None = None
    date: str | None = None
    posted: bool | None = None
    status: str | None = None
    previous_status: str | None = None
    already_approved: bool = False
    changed: bool = False
    auto_approved: bool = False
    sto_ready: bool = False
    sto_issues: list[dict[str, str]] = Field(default_factory=list)
    ud_recommendation: str | None = None
    auto_approve_allowed: bool = False
    approver_fio: str | None = None
    comment: str | None = None
    message: str | None = None


async def approve_service_memo_tool(
    payload: ApproveServiceMemoInput,
    context: ToolContext,
) -> ApproveServiceMemoOutput:
    del context
    raw = await asyncio.to_thread(
        approve_service_memo,
        ref_key=payload.ref_key,
        number=payload.number,
        approver_fio=payload.approver_fio,
        comment=payload.comment,
    )
    return ApproveServiceMemoOutput.model_validate(raw)


class ApproveServiceMemoTool(Tool):
    name = "approve_service_memo"
    description = (
        "Согласовывает служебную записку по совещанию в 1С:ERP "
        "(Статус «НеСогласована» → «Согласована»)."
    )
    agent_description = (
        "Инструмент approve_service_memo проверяет Document_ТД_СлужебнаяЗаписка в 1С:ERP "
        "и условия СТО. Передай ref_key или number (например 000010430). "
        "Возвращает sto_checklist, sto_ready, ud_recommendation для сотрудника УД. "
        "Автосогласование в 1С сейчас отключено — документ не меняется. "
        "approver_fio и comment зарезервированы для ручного согласования. "
        "Нужны ONEC_ODATA_* в .env."
    )
    input_model = ApproveServiceMemoInput
    output_model = ApproveServiceMemoOutput
    required_permissions = ["approve_service_memo"]
    preview_default_params = {"number": "000010430"}

    async def execute(
        self,
        payload: ApproveServiceMemoInput,
        context: ToolContext,
    ) -> ApproveServiceMemoOutput:
        return await approve_service_memo_tool(payload, context)


register_tool(ApproveServiceMemoTool())


class RejectServiceMemoInput(BaseModel):
    ref_key: str | None = Field(default=None, description="Ref_Key служебной записки")
    number: str | None = Field(default=None, description="Номер служебной записки, например 000009853")
    reason: str = Field(description="Причина отклонения")
    rejector_fio: str | None = Field(
        default=None,
        description="ФИО сотрудника УД (Catalog_Пользователи) для поля ИсполнительУД",
    )
    notify_initiator: bool = Field(
        default=True,
        description="Отправить уведомление на рабочий стол 1С инициатору СЗ",
    )
    dry_run: bool = Field(
        default=False,
        description="Только проверить документ, без PATCH и уведомления",
    )


class RejectServiceMemoOutput(BaseModel):
    ref_key: str
    number: str | None = None
    date: str | None = None
    posted: bool | None = None
    status: str | None = None
    previous_status: str | None = None
    already_rejected: bool = False
    changed: bool = False
    notification_sent: bool = False
    dry_run: bool = False
    would_notify_initiator: bool | None = None
    initiator_fio: str | None = None
    initiator_ref: str | None = None
    reason: str
    comment: str | None = None
    rejector_fio: str | None = None
    notification_message: str | None = None
    notification: dict[str, Any] | None = None


async def reject_service_memo_tool(
    payload: RejectServiceMemoInput,
    context: ToolContext,
) -> RejectServiceMemoOutput:
    del context
    raw = await asyncio.to_thread(
        reject_service_memo,
        ref_key=payload.ref_key,
        number=payload.number,
        reason=payload.reason,
        rejector_fio=payload.rejector_fio,
        notify_initiator=payload.notify_initiator,
        dry_run=payload.dry_run,
    )
    return RejectServiceMemoOutput.model_validate(raw)


class RejectServiceMemoTool(Tool):
    name = "reject_service_memo"
    description = (
        "Отклоняет служебную записку по совещанию в 1С:ERP "
        "(Статус «НеСогласована» → «Отклонена») и уведомляет инициатора."
    )
    agent_description = (
        "Инструмент reject_service_memo отклоняет Document_ТД_СлужебнаяЗаписка в 1С:ERP. "
        "Передай ref_key или number (например 000009853) и reason — краткую причину отклонения "
        "(например «Не указана тема совещания» из sto_issues). "
        "В поле Комментарий 1С записывается только reason, без номера СЗ и без текста уведомления. "
        "Статус меняется на «Отклонена». "
        "Инициатору (Ответственный) отправляется уведомление на рабочий стол 1С. "
        "rejector_fio — ФИО сотрудника УД; dry_run=true — только проверка без изменений. "
        "Нужны ONEC_ODATA_* в .env."
    )
    input_model = RejectServiceMemoInput
    output_model = RejectServiceMemoOutput
    required_permissions = ["reject_service_memo"]
    preview_default_params = {
        "number": "000009853",
        "reason": "Не заполнены обязательные поля СТО",
        "dry_run": True,
    }

    async def execute(
        self,
        payload: RejectServiceMemoInput,
        context: ToolContext,
    ) -> RejectServiceMemoOutput:
        return await reject_service_memo_tool(payload, context)


register_tool(RejectServiceMemoTool())


class ProtocolTaskInput(BaseModel):
    item_number: int | None = Field(
        default=None,
        ge=1,
        description="Номер пункта протокола (если не указан — порядковый)",
    )
    text: str = Field(description="Текст пункта / поручения")
    due_date: str | None = Field(
        default=None,
        description="Срок исполнения в ISO-формате",
    )
    responsible_fio: str | None = Field(
        default=None,
        description="ФИО ответственного за пункт",
    )


class CreateProtocolInput(BaseModel):
    number: str = Field(description="Номер протокола, например НСР_001_О_001")
    comment: str = Field(default="", description="Комментарий документа")
    template_ref_key: str | None = Field(
        default=None,
        description="Ref_Key протокола-шаблона для копирования реквизитов",
    )
    template_number_prefix: str | None = Field(
        default=None,
        description="Префикс номера для выбора шаблона, например НСР",
    )
    manager_fio: str | None = Field(default=None, description="ФИО руководителя")
    responsible_fio: str | None = Field(default=None, description="ФИО ответственного")
    prepared_by_fio: str | None = Field(default=None, description="ФИО подготовившего")
    topic_key: str | None = Field(default=None, description="Ref_Key темы совещания")
    meeting_type: str | None = Field(
        default=None,
        description="Вид совещания, например Отчетное",
    )
    tasks: list[ProtocolTaskInput] = Field(
        default_factory=list,
        description="Пункты протокола для регистра задач",
    )


class ProtocolInfo(BaseModel):
    ref_key: str | None = None
    number: str | None = None
    date: str | None = None
    status: str | None = None
    posted: bool | None = None
    comment: str | None = None


class ProtocolTemplateInfo(BaseModel):
    ref_key: str | None = None
    number: str | None = None


class ProtocolTaskInfo(BaseModel):
    item_number: str | int | None = None
    task_id: str | None = None
    text: str | None = None
    due_date: str | None = None
    responsible_ref: str | None = None


class CreateProtocolOutput(BaseModel):
    protocol: ProtocolInfo
    template: ProtocolTemplateInfo
    tasks: list[ProtocolTaskInfo] = Field(default_factory=list)


async def create_protocol_tool(
    payload: CreateProtocolInput,
    context: ToolContext,
) -> CreateProtocolOutput:
    del context
    raw = await asyncio.to_thread(
        create_meeting_protocol,
        number=payload.number,
        comment=payload.comment,
        template_ref_key=payload.template_ref_key,
        template_number_prefix=payload.template_number_prefix,
        manager_fio=payload.manager_fio,
        responsible_fio=payload.responsible_fio,
        prepared_by_fio=payload.prepared_by_fio,
        topic_key=payload.topic_key,
        meeting_type=payload.meeting_type,
        tasks=[task.model_dump(exclude_none=True) for task in payload.tasks],
    )
    return CreateProtocolOutput.model_validate(raw)


class CreateProtocolTool(Tool):
    name = "create_protocol"
    description = "Создаёт протокол совещания в 1С:ERP и при необходимости пункты в регистре задач."
    agent_description = (
        "Инструмент create_protocol создаёт Document_ТД_Протокол в 1С:ERP через OData. "
        "number — номер документа (серия задаётся явно, например НСР_001_О_001); "
        "template_ref_key или template_number_prefix — откуда взять реквизиты по умолчанию; "
        "manager_fio, responsible_fio, prepared_by_fio — переопределение участников; "
        "tasks — пункты для InformationRegister_ТД_ЗадачиПротоколов. "
        "Нужны ONEC_ODATA_* в .env."
    )
    input_model = CreateProtocolInput
    output_model = CreateProtocolOutput
    required_permissions = ["create_protocol"]
    preview_default_params = {
        "number": "НСР_001_О_001",
        "comment": "Тестовый протокол",
        "tasks": [{"text": "Тестовое поручение"}],
    }

    async def execute(
        self,
        payload: CreateProtocolInput,
        context: ToolContext,
    ) -> CreateProtocolOutput:
        return await create_protocol_tool(payload, context)


register_tool(CreateProtocolTool())


class CreateMeetingTopicInput(BaseModel):
    description: str = Field(description="Наименование темы совещания")
    manager_fio: str = Field(description="ФИО руководителя")
    meeting_type: str = Field(
        default="Отчетное",
        description=f"Вид совещания: {', '.join(MEETING_TYPES)}",
    )
    reviewer_fio: str | None = Field(
        default=None,
        description="ФИО проверяющего; по умолчанию совпадает с руководителем",
    )
    closed_date: str | None = Field(
        default=None,
        description="Дата окончания действия темы (ISO). Пусто = бессрочно",
    )
    closed_end_of_year: bool = Field(
        default=False,
        description="Установить дату закрытия на 31.12 текущего года",
    )
    department_key: str | None = Field(default=None, description="GUID подразделения")
    room_key: str | None = Field(default=None, description="GUID переговорной")
    project_key: str | None = Field(default=None, description="GUID проекта")
    committee_key: str | None = Field(default=None, description="GUID комитета")
    organization_key: str | None = Field(default=None, description="GUID организации")
    start_time: str | None = Field(
        default=None,
        description="Время начала в формате 0001-01-01THH:MM:SS",
    )
    end_time: str | None = Field(
        default=None,
        description="Время окончания в формате 0001-01-01THH:MM:SS",
    )
    is_management_circle_topic: bool | None = Field(
        default=None,
        description="Тема круга управления; без значения берётся из шаблона",
    )
    template_ref_key: str | None = Field(
        default=None,
        description="Ref_Key темы-шаблона для копирования реквизитов",
    )
    template_code: str | None = Field(
        default=None,
        description="Код темы-шаблона для копирования реквизитов",
    )
    dry_run: bool = Field(
        default=False,
        description="Сформировать payload без записи в 1С",
    )


class CreateMeetingTopicOutput(BaseModel):
    catalog_entity: str
    dry_run: bool
    manager_fio: str
    reviewer_fio: str
    template_ref_key: str | None = None
    template_code: str | None = None
    topic: MeetingTopicItem | None = None


async def create_meeting_topic_tool(
    payload: CreateMeetingTopicInput,
    context: ToolContext,
) -> CreateMeetingTopicOutput:
    del context
    raw = await asyncio.to_thread(
        create_meeting_topic,
        description=payload.description,
        manager_fio=payload.manager_fio,
        meeting_type=payload.meeting_type,
        reviewer_fio=payload.reviewer_fio,
        closed_date=payload.closed_date,
        closed_end_of_year=payload.closed_end_of_year,
        department_key=payload.department_key,
        room_key=payload.room_key,
        project_key=payload.project_key,
        committee_key=payload.committee_key,
        organization_key=payload.organization_key,
        start_time=payload.start_time,
        end_time=payload.end_time,
        is_management_circle_topic=payload.is_management_circle_topic,
        template_ref_key=payload.template_ref_key,
        template_code=payload.template_code,
        dry_run=payload.dry_run,
    )
    return CreateMeetingTopicOutput.model_validate(raw)


class CreateMeetingTopicTool(Tool):
    name = "create_meeting_topic"
    description = "Создаёт тему совещания в справочнике Catalog_ТД_ТемыСовещаний 1С:ERP."
    agent_description = (
        "Инструмент create_meeting_topic создаёт элемент справочника тем совещаний 1С "
        "(Catalog_ТД_ТемыСовещаний). Обязательны description, manager_fio, meeting_type. "
        "template_code/template_ref_key — скопировать подразделение, кабинет и прочие "
        "реквизиты с существующей темы; closed_end_of_year — активна до конца года. "
        "dry_run=true — только сформировать payload без записи. Нужны ONEC_ODATA_* в .env."
    )
    input_model = CreateMeetingTopicInput
    output_model = CreateMeetingTopicOutput
    required_permissions = ["create_meeting_topic"]
    preview_default_params = {
        "description": "Технический совет",
        "manager_fio": "Соломичева Светлана Викторовна",
        "meeting_type": "Отчетное",
        "template_code": "000009459",
        "dry_run": True,
    }

    async def execute(
        self,
        payload: CreateMeetingTopicInput,
        context: ToolContext,
    ) -> CreateMeetingTopicOutput:
        return await create_meeting_topic_tool(payload, context)


register_tool(CreateMeetingTopicTool())


class MeetingTopicParticipantItem(BaseModel):
    participant_ref_key: str | None = None
    fio: str | None = None
    topic_ref_key: str | None = None


class GetMeetingTopicParticipantsInput(BaseModel):
    topic_ref_key: str | None = Field(default=None, description="Ref_Key темы совещания")
    topic_code: str | None = Field(default=None, description="Код темы, например 000009459")


class GetMeetingTopicParticipantsOutput(BaseModel):
    register_entity: str
    topic_ref_key: str
    topic_code: str | None = None
    topic_description: str | None = None
    participants_count: int
    participants: list[MeetingTopicParticipantItem]


async def get_meeting_topic_participants_tool(
    payload: GetMeetingTopicParticipantsInput,
    context: ToolContext,
) -> GetMeetingTopicParticipantsOutput:
    del context
    raw = await asyncio.to_thread(
        get_meeting_topic_participants,
        topic_ref_key=payload.topic_ref_key,
        topic_code=payload.topic_code,
    )
    return GetMeetingTopicParticipantsOutput.model_validate(raw)


class GetMeetingTopicParticipantsTool(Tool):
    name = "get_meeting_topic_participants"
    description = (
        "Возвращает участников темы совещания из регистра "
        "InformationRegister_ТД_СоответствиеТемыСовещанияИУчастниковСовещаний."
    )
    agent_description = (
        "Инструмент get_meeting_topic_participants читает участников темы совещания 1С "
        "из регистра «Соответствие темы совещания и участников совещаний (ТД)». "
        "Нужен topic_ref_key или topic_code. Нужны ONEC_ODATA_* в .env."
    )
    input_model = GetMeetingTopicParticipantsInput
    output_model = GetMeetingTopicParticipantsOutput
    required_permissions = ["get_meeting_topic_participants"]
    preview_default_params = {"topic_code": "000009459"}

    async def execute(
        self,
        payload: GetMeetingTopicParticipantsInput,
        context: ToolContext,
    ) -> GetMeetingTopicParticipantsOutput:
        return await get_meeting_topic_participants_tool(payload, context)


register_tool(GetMeetingTopicParticipantsTool())


class DeleteProtocolInput(BaseModel):
    ref_key: str | None = Field(default=None, description="Ref_Key протокола")
    number: str | None = Field(default=None, description="Номер протокола")


class DeleteProtocolOutput(BaseModel):
    ref_key: str
    number: str | None = None
    deleted: bool


async def delete_protocol_tool(
    payload: DeleteProtocolInput,
    context: ToolContext,
) -> DeleteProtocolOutput:
    del context
    raw = await asyncio.to_thread(
        delete_meeting_protocol,
        ref_key=payload.ref_key,
        number=payload.number,
    )
    return DeleteProtocolOutput.model_validate(raw)


class DeleteProtocolTool(Tool):
    name = "delete_protocol"
    description = "Удаляет протокол совещания в 1С:ERP по Ref_Key или номеру."
    agent_description = (
        "Инструмент delete_protocol удаляет Document_ТД_Протокол в 1С:ERP через OData. "
        "Нужен ref_key или number. Нужны ONEC_ODATA_* в .env."
    )
    input_model = DeleteProtocolInput
    output_model = DeleteProtocolOutput
    required_permissions = ["delete_protocol"]
    preview_default_params = {"number": "НСР_001_О_001"}

    async def execute(
        self,
        payload: DeleteProtocolInput,
        context: ToolContext,
    ) -> DeleteProtocolOutput:
        return await delete_protocol_tool(payload, context)


register_tool(DeleteProtocolTool())


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
