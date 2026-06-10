from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.knowledge_base.vector_store import vector_store
from app.models.document import Document, DocumentChunk
from app.models.enums import DocumentType
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.schemas.document import ChunkSearchHit
from app.services.embeddings import embedding_service
from app.services.knowledge_base_search_service import KnowledgeBaseSearchService

PUBLIC_ACCESS_SCOPES = {"public", "global", "all", "company"}


async def search_knowledge_base(
    *,
    query: str,
    db: AsyncSession,
    user: User | None = None,
    top_k: int = 5,
    document_types: list[DocumentType] | list[str] | None = None,
    department_ids: list[uuid.UUID] | list[str] | None = None,
    document_version_id: uuid.UUID | str | None = None,
    access_scopes: list[str] | None = None,
    knowledge_base_id: uuid.UUID | str | None = None,
    agent_id: uuid.UUID | str | None = None,
) -> list[ChunkSearchHit]:
    """Search indexed knowledge-base chunks and return source-aware results."""

    normalized_top_k = max(1, min(top_k, 50))
    if knowledge_base_id is not None:
        return await _search_specific_knowledge_base(
            query=query,
            db=db,
            user=user,
            top_k=normalized_top_k,
            knowledge_base_id=knowledge_base_id,
            agent_id=agent_id,
        )

    qdrant_filters = _build_qdrant_filters(
        document_types=document_types,
        document_version_id=document_version_id,
        access_scopes=access_scopes,
    )
    embedding = await embedding_service.embed_text(query)
    raw_hits = await vector_store.search(
        embedding.vector,
        top_k=max(normalized_top_k * 5, normalized_top_k),
        filters=qdrant_filters,
    )
    if not raw_hits:
        return []

    chunk_ids = _chunk_ids_from_hits(raw_hits)
    if not chunk_ids:
        return []

    chunks = await _load_chunks(db, chunk_ids)
    allowed_department_ids = _normalize_uuid_set(department_ids)
    hits: list[ChunkSearchHit] = []
    for hit in raw_hits:
        payload = hit.get("payload") or {}
        chunk_id = _safe_uuid(payload.get("chunk_id"))
        if chunk_id is None:
            continue
        chunk = chunks.get(chunk_id)
        if chunk is None or chunk.document is None:
            continue
        if not _is_allowed_document(
            chunk.document,
            user=user,
            allowed_department_ids=allowed_department_ids,
            payload=payload,
        ):
            continue
        hits.append(_to_search_hit(hit, chunk, payload))
        if len(hits) >= normalized_top_k:
            break
    return hits


async def _search_specific_knowledge_base(
    *,
    query: str,
    db: AsyncSession,
    user: User | None,
    top_k: int,
    knowledge_base_id: uuid.UUID | str,
    agent_id: uuid.UUID | str | None,
) -> list[ChunkSearchHit]:
    if user is None:
        return []
    kb_uuid = knowledge_base_id if isinstance(knowledge_base_id, uuid.UUID) else uuid.UUID(str(knowledge_base_id))
    kb = await db.get(KnowledgeBase, kb_uuid)
    if kb is None:
        return []
    parsed_agent_id = None if agent_id is None else (agent_id if isinstance(agent_id, uuid.UUID) else uuid.UUID(str(agent_id)))
    result = await KnowledgeBaseSearchService(db).search(
        knowledge_base=kb,
        query=query,
        user=user,
        top_k=top_k,
        agent_id=parsed_agent_id,
        include_inaccessible=False,
    )
    return [
        ChunkSearchHit(
            content=hit.content,
            score=hit.score,
            document_id=hit.document_id,
            document_version_id=hit.document_version_id,
            chunk_id=hit.chunk_id,
            document_title=hit.document_title,
            page_number=hit.page_number,
            section_title=hit.section_title,
            metadata=hit.metadata,
        )
        for hit in result.hits
        if hit.accessible
    ]


def _build_qdrant_filters(
    *,
    document_types: list[DocumentType] | list[str] | None,
    document_version_id: uuid.UUID | str | None,
    access_scopes: list[str] | None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {"is_active": True}
    if document_types:
        filters["document_type"] = [
            item.value if isinstance(item, DocumentType) else str(item)
            for item in document_types
        ]
    if document_version_id:
        filters["document_version_id"] = str(document_version_id)
    if access_scopes:
        filters["access_scope"] = access_scopes
    return filters


async def _load_chunks(db: AsyncSession, chunk_ids: list[uuid.UUID]) -> dict[uuid.UUID, DocumentChunk]:
    result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.id.in_(chunk_ids))
        .options(
            selectinload(DocumentChunk.document),
            selectinload(DocumentChunk.document_version),
        )
    )
    return {chunk.id: chunk for chunk in result.scalars().unique().all()}


def _chunk_ids_from_hits(hits: list[dict]) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    for hit in hits:
        chunk_id = _safe_uuid((hit.get("payload") or {}).get("chunk_id"))
        if chunk_id is not None:
            ids.append(chunk_id)
    return ids


def _is_allowed_document(
    document: Document,
    *,
    user: User | None,
    allowed_department_ids: set[uuid.UUID] | None,
    payload: dict[str, Any],
) -> bool:
    if payload.get("is_active") is False:
        return False
    if allowed_department_ids is not None and document.department_id not in allowed_department_ids:
        return False
    if user is None or user.is_superuser:
        return True

    access_scope = str(payload.get("access_scope") or "").lower()
    if access_scope in PUBLIC_ACCESS_SCOPES:
        return True
    return user.department_id is not None and document.department_id == user.department_id


def _to_search_hit(hit: dict, chunk: DocumentChunk, payload: dict[str, Any]) -> ChunkSearchHit:
    document = chunk.document
    version = chunk.document_version
    metadata = {
        **payload,
        **(chunk.metadata_ or chunk.chunk_metadata or {}),
    }
    return ChunkSearchHit(
        content=chunk.content or chunk.text or "",
        score=float(hit.get("score", 0.0)),
        document_id=document.id if document else chunk.document_id,
        document_version_id=version.id if version else chunk.document_version_id,
        chunk_id=chunk.id,
        document_title=document.title if document else payload.get("document_title"),
        document_type=document.document_type if document else None,
        page_number=chunk.page_number,
        section_title=chunk.section_title,
        metadata=metadata,
    )


def _normalize_uuid_set(values: list[uuid.UUID] | list[str] | None) -> set[uuid.UUID] | None:
    if values is None:
        return None
    return {value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)) for value in values}


def _safe_uuid(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        return None
