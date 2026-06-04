from __future__ import annotations

from app.services.embeddings.base import BaseEmbedder
from app.services.embeddings.exceptions import EmbeddingConfigurationError
from app.services.embeddings.schemas import EmbeddingBatchResult, EmbeddingResult


class OpenAIEmbedder(BaseEmbedder):
    provider = "openai"

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise EmbeddingConfigurationError(
            "OpenAI embeddings отключены. Используйте только local BGE-M3 embedder."
        )

    async def embed_text(self, text: str) -> EmbeddingResult:
        raise EmbeddingConfigurationError(
            "OpenAI embeddings отключены. Используйте только local BGE-M3 embedder."
        )

    async def embed_texts(self, texts: list[str]) -> EmbeddingBatchResult:
        raise EmbeddingConfigurationError(
            "OpenAI embeddings отключены. Используйте только local BGE-M3 embedder."
        )
