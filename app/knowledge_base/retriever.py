from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.knowledge_base.vector_store import VectorStore, vector_store
from app.models.document import DocumentChunk
from app.models.knowledge_base import KnowledgeBase
from app.services.embeddings import embedding_service
from app.services.knowledge_base_fts_service import KnowledgeBaseFtsService, merge_hybrid_results


class HybridRetriever:
    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
        db: AsyncSession | None = None,
        *,
        knowledge_base: KnowledgeBase | None = None,
    ) -> list[dict]:
        if knowledge_base is not None and db is not None:
            if settings.KB_SEARCH_MODE.lower() == "hybrid":
                return await self._hybrid_kb_search(db, knowledge_base, query, top_k=top_k)
            return await self._semantic_kb_search(db, knowledge_base, query, top_k=top_k)

        embedding = await embedding_service.embed_text(query)
        hits = await vector_store.search(embedding.vector, top_k=top_k, filters=filters)
        if db is None:
            return hits
        return await self._attach_chunks(db, hits)

    async def _semantic_kb_search(
        self,
        db: AsyncSession,
        knowledge_base: KnowledgeBase,
        query: str,
        *,
        top_k: int,
    ) -> list[dict]:
        """Чистый смысловой поиск: ранжирование только по косинусной близости
        векторов (без полнотекстового поиска по ключевым словам)."""
        fetch_k = max(top_k * 5, top_k)
        embedding = await embedding_service.embed_text(query)
        vector_hits = await VectorStore(knowledge_base.qdrant_collection).search(
            embedding.vector,
            top_k=fetch_k,
            filters={"knowledge_base_id": str(knowledge_base.id), "is_active": True},
        )
        results: list[dict] = []
        for hit in vector_hits:
            payload = hit.get("payload") or {}
            score = float(hit.get("score", 0.0))
            results.append(
                {
                    "id": payload.get("knowledge_base_chunk_id") or hit.get("id"),
                    "score": score,
                    "payload": payload,
                    "hybrid_score": score,
                    "vector_score": score,
                }
            )
        return await self._attach_chunks(db, results)

    async def _hybrid_kb_search(
        self,
        db: AsyncSession,
        knowledge_base: KnowledgeBase,
        query: str,
        *,
        top_k: int,
    ) -> list[dict]:
        fts_service = KnowledgeBaseFtsService(db)
        is_exact = fts_service.is_exact_query(query)
        vector_weight = 0.35 if is_exact else 0.65
        fts_weight = 0.65 if is_exact else 0.35
        fetch_k = max(top_k * 5, top_k)

        embedding = await embedding_service.embed_text(query)
        vector_hits = await VectorStore(knowledge_base.qdrant_collection).search(
            embedding.vector,
            top_k=fetch_k,
            filters={"knowledge_base_id": str(knowledge_base.id), "is_active": True},
        )
        fts_hits = await fts_service.search(knowledge_base.id, query, top_k=fetch_k)
        merged = merge_hybrid_results(
            vector_hits,
            fts_hits,
            top_k=fetch_k,
            vector_weight=vector_weight,
            fts_weight=fts_weight,
        )
        return await self._attach_chunks(db, merged)

    async def _attach_chunks(self, db: AsyncSession, hits: list[dict]) -> list[dict]:
        chunk_ids = []
        for hit in hits:
            payload = hit.get("payload") or {}
            chunk_id = payload.get("chunk_id")
            if chunk_id:
                try:
                    chunk_ids.append(uuid.UUID(str(chunk_id)))
                except ValueError:
                    continue
        if not chunk_ids:
            return hits

        result = await db.execute(select(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids)))
        chunks = {chunk.id: chunk for chunk in result.scalars().all()}
        enriched: list[dict] = []
        for hit in hits:
            payload = hit.get("payload") or {}
            chunk_id = payload.get("chunk_id")
            chunk = chunks.get(uuid.UUID(str(chunk_id))) if chunk_id else None
            if chunk is not None:
                payload = {
                    **payload,
                    "content": chunk.content or chunk.text,
                    "metadata": chunk.metadata_ or chunk.chunk_metadata,
                }
            enriched.append({**hit, "payload": payload})
        return enriched


retriever = HybridRetriever()
