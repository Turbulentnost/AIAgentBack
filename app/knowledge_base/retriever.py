from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge_base.vector_store import vector_store
from app.models.document import DocumentChunk
from app.services.embeddings import embedding_service


class HybridRetriever:
    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
        db: AsyncSession | None = None,
    ) -> list[dict]:
        embedding = await embedding_service.embed_text(query)
        hits = await vector_store.search(embedding.vector, top_k=top_k, filters=filters)
        if db is None:
            return hits
        return await self._attach_chunks(db, hits)

    async def _attach_chunks(self, db: AsyncSession, hits: list[dict]) -> list[dict]:
        chunk_ids = [
            uuid.UUID(str(hit.get("payload", {}).get("chunk_id")))
            for hit in hits
            if hit.get("payload", {}).get("chunk_id")
        ]
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
                    "content": chunk.text or chunk.content,
                    "metadata": chunk.metadata_ or chunk.chunk_metadata,
                }
            enriched.append({**hit, "payload": payload})
        return enriched


retriever = HybridRetriever()
