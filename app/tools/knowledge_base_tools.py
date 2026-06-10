from __future__ import annotations

import uuid

from sqlalchemy import select

from app.tools.base import Tool
from app.tools.registry import register_tool
from app.tools.schemas import (
    AvailableKnowledgeBaseItem,
    GetKnowledgeFragmentInput,
    GetKnowledgeFragmentOutput,
    KnowledgeBaseSearchResult,
    KnowledgeFragmentNeighbor,
    ListAvailableKnowledgeBasesInput,
    ListAvailableKnowledgeBasesOutput,
    SearchKnowledgeBaseInput,
    SearchKnowledgeBaseOutput,
    ToolContext,
)
from app.models.document import Document, DocumentChunk
from app.models.enums import KnowledgeBaseAccessType, KnowledgeBaseAgentAccessMode, KnowledgeBaseStatus
from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseChunk
from app.services.knowledge_base_access_service import KnowledgeBaseAccessService
from app.services.knowledge_base_search_service import KnowledgeBaseSearchService
from app.services.knowledge_base_service import KnowledgeBaseService


async def list_available_knowledge_bases(
    payload: ListAvailableKnowledgeBasesInput,
    context: ToolContext,
) -> ListAvailableKnowledgeBasesOutput:
    kb_service = KnowledgeBaseService(context.db)
    access_service = KnowledgeBaseAccessService(context.db)
    candidates = await kb_service.list_knowledge_bases(status=KnowledgeBaseStatus.READY, query=payload.query)
    items: list[AvailableKnowledgeBaseItem] = []
    for candidate in candidates:
        kb = await access_service.load_for_access_check(candidate.id) or candidate
        effective = await access_service.can_access_knowledge_base(
            user=context.user,
            knowledge_base=kb,
            required_access=KnowledgeBaseAccessType.USE_VIA_AGENT if context.agent_id else KnowledgeBaseAccessType.SEARCH,
            agent_id=context.agent_id,
            required_agent_mode=KnowledgeBaseAgentAccessMode.SEARCH_ONLY,
            allow_non_ready_for_admin=False,
        )
        if not effective.allowed:
            continue
        metadata = kb.metadata_ or {}
        access_scope = metadata.get("access_scope") or (str(kb.department_id) if kb.department_id else None)
        items.append(
            AvailableKnowledgeBaseItem(
                knowledge_base_id=kb.id,
                code=metadata.get("code") or kb.process_slug,
                name=kb.name,
                description=kb.description,
                topic=kb.topic,
                document_count=kb.sources_count,
                fragments_count=kb.fragments_count,
                access_scope=access_scope,
                last_indexed_at=kb.last_indexed_at,
            )
        )
    return ListAvailableKnowledgeBasesOutput(items=items)


async def search_knowledge_base(
    payload: SearchKnowledgeBaseInput,
    context: ToolContext,
) -> SearchKnowledgeBaseOutput:
    kb_ids = payload.knowledge_base_ids
    if not kb_ids:
        available = await list_available_knowledge_bases(ListAvailableKnowledgeBasesInput(), context)
        kb_ids = [item.knowledge_base_id for item in available.items]

    results: list[KnowledgeBaseSearchResult] = []
    search_service = KnowledgeBaseSearchService(context.db)
    for kb_id in kb_ids:
        kb = await context.db.get(KnowledgeBase, kb_id)
        if kb is None:
            continue
        response = await search_service.search(
            knowledge_base=kb,
            query=payload.query,
            user=context.user,
            top_k=payload.top_k,
            agent_id=context.agent_id,
            include_inaccessible=False,
        )
        for hit in response.hits:
            if payload.score_threshold is not None and hit.score < payload.score_threshold:
                continue
            if hit.knowledge_base_chunk_id is None:
                continue
            results.append(
                KnowledgeBaseSearchResult(
                    fragment_id=hit.knowledge_base_chunk_id,
                    document_id=hit.document_id,
                    document_title=hit.document_title,
                    section_title=hit.section_title,
                    page_number=hit.page_number,
                    text=hit.content,
                    score=hit.score,
                    source=_format_source(hit.document_title, hit.page_number),
                    knowledge_base_id=hit.knowledge_base_id,
                )
            )

    results.sort(key=lambda item: item.score, reverse=True)
    return SearchKnowledgeBaseOutput(query=payload.query, results=results[: payload.top_k])


async def get_knowledge_fragment(
    payload: GetKnowledgeFragmentInput,
    context: ToolContext,
) -> GetKnowledgeFragmentOutput:
    row = await _load_fragment_row(context, payload.fragment_id)
    if row is None:
        raise ValueError("Фрагмент базы знаний не найден")
    kb_chunk, document_chunk, document = row
    kb = await KnowledgeBaseService(context.db).get_or_raise(kb_chunk.knowledge_base_id)
    effective = await KnowledgeBaseAccessService(context.db).can_use_chunk(
        user=context.user,
        knowledge_base=kb,
        kb_chunk=kb_chunk,
        document=document,
        agent_id=context.agent_id,
        required_access=KnowledgeBaseAccessType.USE_VIA_AGENT if context.agent_id else KnowledgeBaseAccessType.SEARCH,
        required_agent_mode=KnowledgeBaseAgentAccessMode.SEARCH_ONLY,
    )
    if not effective.allowed:
        raise PermissionError(f"Фрагмент недоступен: {effective.reason}")

    neighbors = []
    if payload.include_neighbors and payload.neighbors_count > 0:
        neighbors = await _load_neighbors(context, kb, document_chunk, payload.neighbors_count)

    return GetKnowledgeFragmentOutput(
        fragment_id=kb_chunk.id,
        knowledge_base_id=kb.id,
        document_id=document.id if document else document_chunk.document_id,
        document_title=document.title if document else None,
        section_title=document_chunk.section_title,
        page_number=document_chunk.page_number,
        text=document_chunk.content or document_chunk.text or "",
        neighbors=neighbors,
        source=_format_source(document.title if document else None, document_chunk.page_number),
        metadata={
            "chunk_id": str(document_chunk.id),
            "document_version_id": str(document_chunk.document_version_id),
            "clause_number": kb_chunk.clause_number,
            "fragment_type": kb_chunk.fragment_type,
            **(document_chunk.metadata_ or document_chunk.chunk_metadata or {}),
        },
    )


async def _load_fragment_row(
    context: ToolContext,
    fragment_id: uuid.UUID,
) -> tuple[KnowledgeBaseChunk, DocumentChunk, Document | None] | None:
    result = await context.db.execute(
        select(KnowledgeBaseChunk, DocumentChunk, Document)
        .join(DocumentChunk, DocumentChunk.id == KnowledgeBaseChunk.document_chunk_id)
        .outerjoin(Document, Document.id == DocumentChunk.document_id)
        .where(KnowledgeBaseChunk.id == fragment_id)
    )
    return result.one_or_none()


async def _load_neighbors(
    context: ToolContext,
    knowledge_base: KnowledgeBase,
    document_chunk: DocumentChunk,
    neighbors_count: int,
) -> list[KnowledgeFragmentNeighbor]:
    min_index = document_chunk.chunk_index - neighbors_count
    max_index = document_chunk.chunk_index + neighbors_count
    result = await context.db.execute(
        select(KnowledgeBaseChunk, DocumentChunk, Document)
        .join(DocumentChunk, DocumentChunk.id == KnowledgeBaseChunk.document_chunk_id)
        .outerjoin(Document, Document.id == DocumentChunk.document_id)
        .where(
            KnowledgeBaseChunk.knowledge_base_id == knowledge_base.id,
            DocumentChunk.document_version_id == document_chunk.document_version_id,
            DocumentChunk.chunk_index >= min_index,
            DocumentChunk.chunk_index <= max_index,
            DocumentChunk.id != document_chunk.id,
        )
        .order_by(DocumentChunk.chunk_index.asc())
    )
    access = KnowledgeBaseAccessService(context.db)
    items: list[KnowledgeFragmentNeighbor] = []
    for kb_chunk, neighbor_chunk, document in result.all():
        effective = await access.can_use_chunk(
            user=context.user,
            knowledge_base=knowledge_base,
            kb_chunk=kb_chunk,
            document=document,
            agent_id=context.agent_id,
            required_access=KnowledgeBaseAccessType.USE_VIA_AGENT if context.agent_id else KnowledgeBaseAccessType.SEARCH,
            required_agent_mode=KnowledgeBaseAgentAccessMode.SEARCH_ONLY,
        )
        if not effective.allowed:
            continue
        items.append(
            KnowledgeFragmentNeighbor(
                fragment_id=kb_chunk.id,
                text=neighbor_chunk.content or neighbor_chunk.text or "",
                page_number=neighbor_chunk.page_number,
                section_title=neighbor_chunk.section_title,
            )
        )
    return items


def _format_source(document_title: str | None, page_number: int | None) -> str:
    if document_title and page_number:
        return f"{document_title}, стр. {page_number}"
    if document_title:
        return document_title
    if page_number:
        return f"стр. {page_number}"
    return "Источник не указан"


class ListAvailableKnowledgeBasesTool(Tool):
    name = "list_available_knowledge_bases"
    description = "Возвращает базы знаний, доступные текущему пользователю и агенту."
    agent_description = (
        "Инструмент list_available_knowledge_bases возвращает список баз знаний, доступных текущему "
        "пользователю и агенту. Используй его, когда нужно понять, какие источники знаний можно использовать "
        "для решения задачи. Инструмент возвращает только разрешенные базы знаний с названием, описанием, "
        "тематикой и количеством документов. Не используй базы знаний, которых нет в результате этого инструмента."
    )
    input_model = ListAvailableKnowledgeBasesInput
    output_model = ListAvailableKnowledgeBasesOutput
    required_permissions = ["knowledge_base.list"]
    preview_safe = True
    preview_default_params = {"query": None}

    async def execute(
        self, payload: ListAvailableKnowledgeBasesInput, context: ToolContext
    ) -> ListAvailableKnowledgeBasesOutput:
        return await list_available_knowledge_bases(payload, context)


class SearchKnowledgeBaseTool(Tool):
    name = "search_knowledge_base"
    description = "Выполняет семантический поиск по разрешенным базам знаний."
    agent_description = (
        "Инструмент search_knowledge_base выполняет семантический поиск по разрешенным базам знаний и "
        "возвращает релевантные фрагменты документов с источниками. Используй его, когда нужно найти "
        "требования, правила, определения, инструкции, нормы, шаблоны или технические сведения. Инструмент "
        "возвращает только фрагменты, доступные текущему пользователю и агенту."
    )
    input_model = SearchKnowledgeBaseInput
    output_model = SearchKnowledgeBaseOutput
    required_permissions = ["knowledge_base.search"]
    preview_safe = True

    async def execute(self, payload: SearchKnowledgeBaseInput, context: ToolContext) -> SearchKnowledgeBaseOutput:
        return await search_knowledge_base(payload, context)


class GetKnowledgeFragmentTool(Tool):
    name = "get_knowledge_fragment"
    description = "Возвращает выбранный фрагмент базы знаний с соседними chunks."
    agent_description = (
        "Инструмент get_knowledge_fragment возвращает выбранный фрагмент базы знаний по fragment_id или "
        "chunk_id. Используй его, если нужно подробнее прочитать найденный источник, получить соседние "
        "фрагменты или уточнить контекст перед формированием вывода. Инструмент не выполняет поиск, а "
        "возвращает конкретный разрешенный фрагмент."
    )
    input_model = GetKnowledgeFragmentInput
    output_model = GetKnowledgeFragmentOutput
    required_permissions = ["knowledge_base.read_fragment"]

    async def execute(self, payload: GetKnowledgeFragmentInput, context: ToolContext) -> GetKnowledgeFragmentOutput:
        return await get_knowledge_fragment(payload, context)


register_tool(ListAvailableKnowledgeBasesTool())
register_tool(SearchKnowledgeBaseTool())
register_tool(GetKnowledgeFragmentTool())
