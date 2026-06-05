from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.knowledge_base.vector_store import VectorStore
from app.models.document import Document, DocumentChunk
from app.models.enums import KnowledgeBaseAccessType, KnowledgeBaseAgentAccessMode
from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseChunk
from app.models.user import User
from app.schemas.knowledge_base import KnowledgeBaseSearchHit, KnowledgeBaseTestSearchResponse
from app.services.embeddings import embedding_service
from app.services.knowledge_base_access_service import KnowledgeBaseAccessService


class KnowledgeBaseSearchService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.access = KnowledgeBaseAccessService(db)

    async def search(
        self,
        *,
        knowledge_base: KnowledgeBase,
        query: str,
        user: User,
        top_k: int = 5,
        agent_id: uuid.UUID | None = None,
        include_inaccessible: bool = False,
    ) -> KnowledgeBaseTestSearchResponse:
        normalized_top_k = max(1, min(top_k, 50))
        embedding = await embedding_service.embed_text(query)
        raw_hits = await VectorStore(knowledge_base.qdrant_collection).search(
            embedding.vector,
            top_k=max(normalized_top_k * 5, normalized_top_k),
            filters={"knowledge_base_id": str(knowledge_base.id), "is_active": True},
        )

        kb_chunk_ids = _kb_chunk_ids(raw_hits)
        rows = await self._load_rows(kb_chunk_ids)
        hits: list[KnowledgeBaseSearchHit] = []
        for raw_hit in raw_hits:
            payload = raw_hit.get("payload") or {}
            kb_chunk_id = _safe_uuid(payload.get("knowledge_base_chunk_id") or raw_hit.get("id"))
            if kb_chunk_id is None:
                continue
            row = rows.get(kb_chunk_id)
            if row is None:
                continue
            kb_chunk, document_chunk, document = row
            effective = await self.access.can_use_chunk(
                user=user,
                knowledge_base=knowledge_base,
                kb_chunk=kb_chunk,
                document=document,
                agent_id=agent_id,
                required_access=KnowledgeBaseAccessType.USE_VIA_AGENT if agent_id else KnowledgeBaseAccessType.SEARCH,
                required_agent_mode=KnowledgeBaseAgentAccessMode.SEARCH_ONLY,
            )
            if not effective.allowed and not include_inaccessible:
                continue
            content = document_chunk.text or document_chunk.content or ""
            hits.append(
                KnowledgeBaseSearchHit(
                    content=content if effective.allowed or user.is_superuser else "(фрагмент недоступен текущему сценарию)",
                    score=float(raw_hit.get("score", 0.0)),
                    accessible=effective.allowed,
                    access_reason=effective.reason,
                    knowledge_base_id=knowledge_base.id,
                    knowledge_base_chunk_id=kb_chunk.id,
                    document_id=document.id if document else document_chunk.document_id,
                    document_version_id=document_chunk.document_version_id,
                    chunk_id=document_chunk.id,
                    document_title=document.title if document else payload.get("document_title"),
                    page_number=document_chunk.page_number,
                    section_title=document_chunk.section_title,
                    clause_number=kb_chunk.clause_number,
                    metadata={**payload, **(document_chunk.metadata_ or document_chunk.chunk_metadata or {})},
                )
            )
            if len([hit for hit in hits if hit.accessible]) >= normalized_top_k:
                break

        accessible_hits = [hit for hit in hits if hit.accessible]
        return KnowledgeBaseTestSearchResponse(
            hits=hits[:normalized_top_k] if include_inaccessible else accessible_hits[:normalized_top_k],
            answer_preview=self._answer_preview(accessible_hits),
        )

    async def _load_rows(
        self,
        kb_chunk_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, tuple[KnowledgeBaseChunk, DocumentChunk, Document | None]]:
        if not kb_chunk_ids:
            return {}
        result = await self.db.execute(
            select(KnowledgeBaseChunk, DocumentChunk, Document)
            .join(DocumentChunk, DocumentChunk.id == KnowledgeBaseChunk.document_chunk_id)
            .outerjoin(Document, Document.id == DocumentChunk.document_id)
            .where(KnowledgeBaseChunk.id.in_(kb_chunk_ids))
            .options(selectinload(KnowledgeBaseChunk.source))
        )
        return {kb_chunk.id: (kb_chunk, document_chunk, document) for kb_chunk, document_chunk, document in result.all()}

    def _answer_preview(self, hits: list[KnowledgeBaseSearchHit]) -> str | None:
        if not hits:
            return None
        snippets = []
        for hit in hits[:3]:
            text = " ".join(hit.content.split())
            if text:
                snippets.append(text[:300])
        if not snippets:
            return None
        return " ".join(snippets)


def _kb_chunk_ids(hits: list[dict[str, Any]]) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    for hit in hits:
        payload = hit.get("payload") or {}
        value = payload.get("knowledge_base_chunk_id") or hit.get("id")
        parsed = _safe_uuid(value)
        if parsed is not None:
            ids.append(parsed)
    return ids


def _safe_uuid(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        return None
