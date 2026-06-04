from __future__ import annotations
from app.knowledge_base.vector_store import vector_store
from app.llm.gateway import llm_gateway
class HybridRetriever:
    async def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        embeddings = await llm_gateway.embeddings([query])
        return await vector_store.search(embeddings[0], top_k=top_k)
retriever = HybridRetriever()
