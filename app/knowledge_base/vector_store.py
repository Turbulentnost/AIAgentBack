from __future__ import annotations

from app.core.config import settings
from app.integrations.qdrant import QdrantVectorClient, qdrant_client


class VectorStore:
    def __init__(self, collection: str | None = None) -> None:
        self.collection = collection or settings.QDRANT_COLLECTION
        self._qdrant: QdrantVectorClient = (
            qdrant_client if self.collection == settings.QDRANT_COLLECTION else QdrantVectorClient(self.collection)
        )

    async def ensure_collection(self, vector_size: int | None = None) -> None:
        await self._qdrant.ensure_collection(collection=self.collection, vector_size=vector_size)

    async def search(
        self,
        vector: list[float],
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        results = await self._qdrant.search(
            vector,
            collection=self.collection,
            top_k=top_k,
            filters=filters,
        )
        return [
            {"id": item.id, "score": item.score, "payload": item.payload}
            for item in results
        ]


vector_store = VectorStore()
