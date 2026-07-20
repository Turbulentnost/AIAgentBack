from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.chunk_utils import chunk_embedding_text
from app.models.document import DocumentChunk
from app.models.knowledge_base import KnowledgeBaseChunk


EXACT_QUERY_PATTERN = re.compile(
    r"(?:гост|сто|ту|рг|пл|пункт|п\.|§|версия|v\d|м³/ч|м3/ч|ufg|спу|dn|pn|qmax|g6)",
    re.IGNORECASE,
)


class KnowledgeBaseFtsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def index_chunk(self, kb_chunk: KnowledgeBaseChunk, document_chunk: DocumentChunk) -> None:
        content = chunk_embedding_text(document_chunk)
        if not content.strip():
            return
        clause = kb_chunk.clause_number or ""
        section = document_chunk.section_title or ""
        combined = f"{clause} {section} {content}".strip()
        await self.db.execute(
            text(
                """
                UPDATE knowledge_base_chunks
                SET search_vector = to_tsvector('russian', :content)
                WHERE id = :chunk_id
                """
            ),
            {"content": combined[:100000], "chunk_id": str(kb_chunk.id)},
        )

    async def search(
        self,
        knowledge_base_id: uuid.UUID,
        query: str,
        *,
        top_k: int = 20,
    ) -> list[dict]:
        # websearch_to_tsquery объединяет слова через AND: вопрос целиком
        # («Какие есть базы данных») часто не находит ничего. Если строгий
        # вариант пуст, повторяем поиск с OR между словами.
        hits = await self._search_tsquery(knowledge_base_id, query, top_k=top_k)
        if hits:
            return hits
        or_query = " OR ".join(word for word in query.split() if word)
        if or_query and or_query != query:
            return await self._search_tsquery(knowledge_base_id, or_query, top_k=top_k)
        return []

    async def _search_tsquery(
        self,
        knowledge_base_id: uuid.UUID,
        query: str,
        *,
        top_k: int,
    ) -> list[dict]:
        stmt = (
            select(
                KnowledgeBaseChunk.id,
                func.ts_rank(KnowledgeBaseChunk.search_vector, func.websearch_to_tsquery("russian", query)).label(
                    "rank"
                ),
            )
            .where(
                KnowledgeBaseChunk.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseChunk.is_excluded_from_search.is_(False),
                KnowledgeBaseChunk.search_vector.is_not(None),
                KnowledgeBaseChunk.search_vector.op("@@")(func.websearch_to_tsquery("russian", query)),
            )
            .order_by(text("rank DESC"))
            .limit(top_k)
        )
        result = await self.db.execute(stmt)
        return [{"knowledge_base_chunk_id": row[0], "score": float(row[1] or 0.0), "source": "fts"} for row in result.all()]

    @staticmethod
    def is_exact_query(query: str) -> bool:
        if EXACT_QUERY_PATTERN.search(query):
            return True
        if re.search(r"\d+\.\d+(?:\.\d+)*", query):
            return True
        if re.search(r"[A-Z]{2,}[-_]?\w*", query):
            return True
        return False


def merge_hybrid_results(
    vector_hits: list[dict],
    fts_hits: list[dict],
    *,
    top_k: int,
    vector_weight: float = 0.6,
    fts_weight: float = 0.4,
    rrf_k: int = 60,
) -> list[dict]:
    """Reciprocal Rank Fusion merge of vector and FTS hits."""
    scores: dict[str, float] = {}
    payloads: dict[str, dict] = {}

    for rank, hit in enumerate(vector_hits):
        kb_id = _extract_kb_chunk_id(hit)
        if kb_id is None:
            continue
        key = str(kb_id)
        scores[key] = scores.get(key, 0.0) + vector_weight / (rrf_k + rank + 1)
        payloads[key] = {**hit, "vector_score": hit.get("score", 0.0)}

    for rank, hit in enumerate(fts_hits):
        kb_id = hit.get("knowledge_base_chunk_id")
        if kb_id is None:
            continue
        key = str(kb_id)
        scores[key] = scores.get(key, 0.0) + fts_weight / (rrf_k + rank + 1)
        payloads[key] = {**payloads.get(key, {}), **hit, "fts_score": hit.get("score", 0.0)}

    merged = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]
    results: list[dict] = []
    for key, score in merged:
        payload = payloads.get(key, {})
        results.append(
            {
                "id": key,
                "score": score,
                "payload": payload.get("payload") or {"knowledge_base_chunk_id": key},
                "hybrid_score": score,
                "vector_score": payload.get("vector_score"),
                "fts_score": payload.get("fts_score"),
            }
        )
    return results


def _extract_kb_chunk_id(hit: dict) -> uuid.UUID | None:
    payload = hit.get("payload") or {}
    value = payload.get("knowledge_base_chunk_id") or hit.get("id")
    if not value:
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        return None
