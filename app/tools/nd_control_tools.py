from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.schemas.nd_control_registry import NdControlDepartmentRead, NdDocumentCardRead
from app.tools.base import Tool
from app.tools.registry import register_tool
from app.tools.schemas import ToolContext


class ListNdControlDepartmentsInput(BaseModel):
    pass


class ListNdControlDepartmentsOutput(BaseModel):
    departments: list[NdControlDepartmentRead]


class ListNdDocumentCardsInput(BaseModel):
    department_id: str | None = None
    knowledge_base_id: str | None = None
    query: str | None = None
    page: int = Field(default=1, ge=1)
    size: int = Field(default=50, ge=1, le=200)


class ListNdDocumentCardsOutput(BaseModel):
    items: list[NdDocumentCardRead]
    total: int
    page: int
    size: int


class GetNdDocumentCardInput(BaseModel):
    card_id: str


class GetNdDocumentCardOutput(BaseModel):
    card: NdDocumentCardRead


async def list_nd_control_departments(
    _payload: ListNdControlDepartmentsInput,
    context: ToolContext,
) -> ListNdControlDepartmentsOutput:
    from app.services.nd_control_department_service import NdControlDepartmentService

    items = await NdControlDepartmentService(context.db).list_departments()
    departments = []
    for item in items:
        dept = item["department"]
        base = NdControlDepartmentRead.model_validate(dept)
        departments.append(
            base.model_copy(
                update={
                    "knowledge_bases_count": item["knowledge_bases_count"],
                    "cards_count": item["cards_count"],
                    "knowledge_base_ids": item["knowledge_base_ids"],
                }
            )
        )
    return ListNdControlDepartmentsOutput(departments=departments)


async def list_nd_document_cards(
    payload: ListNdDocumentCardsInput,
    context: ToolContext,
) -> ListNdDocumentCardsOutput:
    from app.services.nd_document_card_service import NdDocumentCardService

    dept_id = uuid.UUID(payload.department_id) if payload.department_id else None
    kb_id = uuid.UUID(payload.knowledge_base_id) if payload.knowledge_base_id else None
    cards, total = await NdDocumentCardService(context.db).list_cards(
        department_id=dept_id,
        knowledge_base_id=kb_id,
        query=payload.query,
        page=payload.page,
        size=payload.size,
    )
    return ListNdDocumentCardsOutput(
        items=[NdDocumentCardRead.model_validate(card) for card in cards],
        total=total,
        page=payload.page,
        size=payload.size,
    )


async def get_nd_document_card(
    payload: GetNdDocumentCardInput,
    context: ToolContext,
) -> GetNdDocumentCardOutput:
    from app.services.nd_document_card_service import NdDocumentCardService, NdDocumentCardServiceError

    try:
        card = await NdDocumentCardService(context.db).get_card_or_raise(uuid.UUID(payload.card_id))
    except NdDocumentCardServiceError as exc:
        raise ValueError(str(exc)) from exc
    return GetNdDocumentCardOutput(card=NdDocumentCardRead.model_validate(card))


class ListNdControlDepartmentsTool(Tool):
    name = "list_nd_control_departments"
    description = "Возвращает отделы агента контроля НД и привязанные базы знаний."
    agent_description = (
        "Инструмент list_nd_control_departments возвращает список отделов агента контроля НД "
        "с привязанными базами знаний и количеством карточек документов."
    )
    input_model = ListNdControlDepartmentsInput
    output_model = ListNdControlDepartmentsOutput
    required_permissions = ["list_nd_control_departments"]

    async def execute(
        self,
        payload: ListNdControlDepartmentsInput,
        context: ToolContext,
    ) -> ListNdControlDepartmentsOutput:
        return await list_nd_control_departments(payload, context)


class ListNdDocumentCardsTool(Tool):
    name = "list_nd_document_cards"
    description = "Возвращает карточки нормативных документов агента контроля НД."
    agent_description = (
        "Инструмент list_nd_document_cards возвращает реестр карточек документов "
        "по отделу агента, базе знаний или поисковому запросу."
    )
    input_model = ListNdDocumentCardsInput
    output_model = ListNdDocumentCardsOutput
    required_permissions = ["list_nd_document_cards"]

    async def execute(
        self,
        payload: ListNdDocumentCardsInput,
        context: ToolContext,
    ) -> ListNdDocumentCardsOutput:
        return await list_nd_document_cards(payload, context)


class GetNdDocumentCardTool(Tool):
    name = "get_nd_document_card"
    description = "Возвращает полную карточку нормативного документа."
    agent_description = (
        "Инструмент get_nd_document_card возвращает полную карточку документа "
        "с метаданными СМК: код, вид, уровень, статус, связи и историю."
    )
    input_model = GetNdDocumentCardInput
    output_model = GetNdDocumentCardOutput
    required_permissions = ["get_nd_document_card"]

    async def execute(self, payload: GetNdDocumentCardInput, context: ToolContext) -> GetNdDocumentCardOutput:
        return await get_nd_document_card(payload, context)


register_tool(ListNdControlDepartmentsTool())
register_tool(ListNdDocumentCardsTool())
register_tool(GetNdDocumentCardTool())
