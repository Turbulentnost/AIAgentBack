from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.documents.chunk_utils import chunk_display_text, chunk_embedding_text
from app.knowledge_base.retriever import HybridRetriever
from app.llm.gateway import llm_gateway
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
            relevance = _relevance_score(raw_hit)
            if settings.KB_SEARCH_MIN_SCORE > 0 and relevance < settings.KB_SEARCH_MIN_SCORE:
                continue
            content = chunk_display_text(document_chunk)
            embedding_text = chunk_embedding_text(document_chunk)
            show_content = effective.allowed or can_view_restricted
            hits.append(
                KnowledgeBaseSearchHit(
                    content=content if show_content else "(фрагмент недоступен текущему сценарию)",
                    score=relevance,
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
                        "embedding_text": embedding_text if embedding_text != content else None,
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
            answer_preview=await self._answer_preview(preview_hits, query),
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

    async def _answer_preview(self, hits: list[KnowledgeBaseSearchHit], query: str) -> str | None:
        # Контекст для LLM: берём топ-фрагменты как есть (в т.ч. структурные,
        # вроде дерева каталогов) — иногда именно там лежит ответ (например,
        # каталог qdrant/). Лишние «рисующие» символы схлопываем.
        context_snippets: list[str] = []
        for hit in hits[:4]:
            meta = hit.metadata or {}
            source_text = meta.get("embedding_text") or hit.content or ""
            text = " ".join(source_text.split())
            if text:
                context_snippets.append(text[:500])
        if not context_snippets:
            return None

        llm_answer = await self._generate_grounded_answer(query, context_snippets)
        if llm_answer:
            return llm_answer
        # Fallback: самый релевантный осмысленный фрагмент (до конца предложения),
        # а если прозы нет — первый фрагмент как есть.
        prose = next((s for s in context_snippets if _is_prose(s)), context_snippets[0])
        return _trim_to_sentence(prose, 400)

    async def _generate_grounded_answer(self, query: str, snippets: list[str]) -> str | None:
        context = "\n\n".join(f"[Фрагмент {index + 1}]\n{snippet}" for index, snippet in enumerate(snippets))
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты ассистент по базе знаний. Отвечай на вопрос кратко и по делу, "
                    "СТРОГО на основании приведённых фрагментов. Не используй внешние знания "
                    "и ничего не выдумывай. Если во фрагментах нет прямого ответа, честно "
                    "напиши, что в документах это явно не указано, и приведи наиболее близкую "
                    "по смыслу информацию из фрагментов. Отвечай на русском языке. /no_think"
                ),
            },
            {
                "role": "user",
                "content": f"Вопрос: {query}\n\nФрагменты из базы знаний:\n{context}\n\nОтвет:",
            },
        ]
        try:
            # Запас токенов нужен, т.к. reasoning-модель (qwen3) сначала
            # «думает», и только потом пишет финальный ответ в content.
            # Ограничиваем время, чтобы поиск не зависал на медленных ответах.
            response = await asyncio.wait_for(
                llm_gateway.chat(messages, temperature=0.1, max_tokens=3000),
                timeout=settings.KB_SEARCH_ANSWER_TIMEOUT,
            )
            message = response["choices"][0]["message"]
            content = message.get("content") or ""
        except Exception:
            return None
        # Сырые рассуждения (reasoning_content) не используем — только чистый
        # ответ. Если ответа нет, вернём None и сработает fallback-фрагмент.
        return _clean_llm_text(content)


_SENTENCE_END = re.compile(r"[.!?…](?:\s|$)")


def _is_prose(text: str) -> bool:
    """Фрагмент считается осмысленным текстом, а не деревом каталогов/кодом.

    Отсеиваем фрагменты, в которых мало букв относительно общей длины
    (например, «│ ├── docker-compose.yml │ ├── nginx/ ...»).
    """
    if not text:
        return False
    letters = sum(1 for ch in text if ch.isalpha())
    if letters < 40:
        return False
    return letters / len(text) >= 0.55


def _trim_to_sentence(text: str | None, limit: int) -> str | None:
    """Обрезает текст до конца последнего целого предложения в пределах limit."""
    if not text:
        return None
    if len(text) <= limit:
        return text
    window = text[:limit]
    matches = list(_SENTENCE_END.finditer(window))
    if matches:
        return window[: matches[-1].end()].strip()
    return window.rsplit(" ", 1)[0].strip() + "…"


def _clean_llm_text(text: str | None) -> str | None:
    if not text:
        return None
    # Убираем блоки рассуждений qwen/reasoning-моделей вида <think>...</think>.
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = cleaned.strip()
    return cleaned or None


def _relevance_score(raw_hit: dict[str, Any]) -> float:
    """Понятная пользователю релевантность 0..1.

    Для гибридного поиска RRF-балл (hybrid_score) очень мал по своей природе
    (≈ 0.01) и не отражает качество совпадения, поэтому для отображения
    используем косинусную близость вектора (bge-m3, 0..1). Если фрагмент найден
    только полнотекстовым поиском, нормализуем ранг ts_rank.
    """
    vector_score = raw_hit.get("vector_score")
    if vector_score is not None:
        return max(0.0, min(1.0, float(vector_score)))
    fts_score = raw_hit.get("fts_score")
    if fts_score is not None:
        return max(0.0, min(1.0, float(fts_score)))
    return float(raw_hit.get("hybrid_score") or raw_hit.get("score", 0.0))


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
