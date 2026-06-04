from __future__ import annotations
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels
from app.core.config import settings
DEFAULT_VECTOR_SIZE = 1536
class VectorStore:
    def __init__(self, collection: str | None = None) -> None:
        self.collection = collection or settings.QDRANT_COLLECTION
        self._client: AsyncQdrantClient | None = None
    @property
    def client(self) -> AsyncQdrantClient:
        if self._client is None:
            self._client = AsyncQdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
        return self._client
    async def ensure_collection(self, vector_size: int = DEFAULT_VECTOR_SIZE) -> None:
        if not await self.client.collection_exists(self.collection):
            await self.client.create_collection(collection_name=self.collection, vectors_config=qmodels.VectorParams(size=vector_size, distance=qmodels.Distance.COSINE))
    async def search(self, vector: list[float], top_k: int = 5) -> list[dict]:
        results = await self.client.search(collection_name=self.collection, query_vector=vector, limit=top_k)
        return [{"id": str(r.id), "score": r.score, "payload": r.payload} for r in results]
vector_store = VectorStore()
