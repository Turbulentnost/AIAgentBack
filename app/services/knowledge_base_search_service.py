from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.knowledge_base.retriever import HybridRetriever
from app.models.document import Document, DocumentChunk
from app.models.enums import (
    KnowledgeBaseAccessType,
    KnowledgeBaseAgentAccessMode,
    KnowledgeBaseChunkQualityStatus,
    KnowledgeBaseSourceStatus,
)
from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseChunk, KnowledgeBaseIndexingError, KnowledgeBaseSource
from app.models.user import User
from app.schemas.knowledge_base import KnowledgeBaseSearchHit, KnowledgeBaseTestSearchResponse
from app.services.knowledge_base_access_service import KnowledgeBaseAccessService


class KnowledgeBaseSearchService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.access = KnowledgeBaseAccessService(db)
        self.retriever = HybridRetriever()

    async def search(
        self,
        *,
        knowledge_base: KnowledgeBase,
        query: str,
        user: User,
        top_k: int = 5,
        agent_id: uuid.UUID | None = None,
        include_inaccessible: bool = False,
        test_mode: bool = False,
        viewer: User | None = None,
    ) -> KnowledgeBaseTestSearchResponse:
        normalized_top_k = max(1, min(top_k, 50))
        viewer_user = viewer or user
        can_view_restricted = False
        if test_mode:
            can_view_restricted = await self._can_view_restricted_test_hits(viewer_user, knowledge_base)
        raw_hits = await self.retriever.retrieve(
            query,
            top_k=max(normalized_top_k * 5, normalized_top_k),
            db=self.db,
            knowledge_base=knowledge_base,
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
            if kb_chunk.is_excluded_from_search:
                continue
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
            show_content = effective.allowed or can_view_restricted
            hits.append(
                KnowledgeBaseSearchHit(
                    content=content if show_content else "(фрагмент недоступен текущему сценарию)",
                    score=float(raw_hit.get("hybrid_score") or raw_hit.get("score", 0.0)),
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
                    metadata={
                        **payload,
                        **(document_chunk.metadata_ or document_chunk.chunk_metadata or {}),
                        "quality_status": getattr(kb_chunk.quality_status, "value", kb_chunk.quality_status),
                    },
                )
            )
            if include_inaccessible:
                if len(hits) >= normalized_top_k:
                    break
            elif len([hit for hit in hits if hit.accessible]) >= normalized_top_k:
                break

        accessible_hits = [hit for hit in hits if hit.accessible]
        preview_hits = accessible_hits if not test_mode else [hit for hit in hits if hit.content and not hit.content.startswith("(фрагмент")]
        return KnowledgeBaseTestSearchResponse(
            hits=hits[:normalized_top_k] if include_inaccessible else accessible_hits[:normalized_top_k],
            answer_preview=self._answer_preview(preview_hits),
        )

    async def _can_view_restricted_test_hits(self, viewer: User, knowledge_base: KnowledgeBase) -> bool:
        if viewer.is_superuser:
            return True
        if knowledge_base.owner_user_id == viewer.id or knowledge_base.responsible_user_id == viewer.id:
            return True
        test_access = await self.access.can_access_knowledge_base(
            user=viewer,
            knowledge_base=knowledge_base,
            required_access=KnowledgeBaseAccessType.SEARCH,
            allow_non_ready_for_admin=True,
        )
        return test_access.allowed

    async def readiness_assessment(self, knowledge_base_id: uuid.UUID) -> dict[str, Any]:
        sources_total = await self.db.scalar(
            select(func.count(KnowledgeBaseSource.id)).where(KnowledgeBaseSource.knowledge_base_id == knowledge_base_id)
        )
        sources_ready = await self.db.scalar(
            select(func.count(KnowledgeBaseSource.id)).where(
                KnowledgeBaseSource.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseSource.processing_status.in_(
                    [KnowledgeBaseSourceStatus.READY, KnowledgeBaseSourceStatus.READY_TO_INDEX]
                ),
            )
        )
        fragments = await self.db.scalar(
            select(func.count(KnowledgeBaseChunk.id)).where(KnowledgeBaseChunk.knowledge_base_id == knowledge_base_id)
        )
        fts_chunks = await self.db.scalar(
            select(func.count(KnowledgeBaseChunk.id)).where(
                KnowledgeBaseChunk.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseChunk.search_vector.is_not(None),
            )
        )
        good_chunks = await self.db.scalar(
            select(func.count(KnowledgeBaseChunk.id)).where(
                KnowledgeBaseChunk.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseChunk.quality_status == KnowledgeBaseChunkQualityStatus.GOOD,
            )
        )
        errors = await self.db.scalar(
            select(func.count(KnowledgeBaseIndexingError.id)).where(
                KnowledgeBaseIndexingError.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseIndexingError.is_resolved.is_(False),
            )
        )
        quality_percent = round((float(good_chunks or 0) / float(fragments or 1)) * 100, 1)
        can_promote = (
            int(sources_ready or 0) > 0
            and int(fragments or 0) > 0
            and int(fts_chunks or 0) > 0
            and int(errors or 0) == 0
            and quality_percent >= 70
        )
        return {
            "sources_total": int(sources_total or 0),
            "sources_ready": int(sources_ready or 0),
            "fragments_total": int(fragments or 0),
            "fts_chunks": int(fts_chunks or 0),
            "quality_percent": quality_percent,
            "unresolved_errors": int(errors or 0),
            "can_promote_to_ready": can_promote,
            "recommendation": "Можно переводить в готовую" if can_promote else "Нужна проверка",
        }

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
