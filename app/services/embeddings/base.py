from __future__ import annotations

import abc

from app.services.embeddings.schemas import EmbeddingBatchResult, EmbeddingResult


class BaseEmbedder(abc.ABC):
    provider: str
    model_name: str
    vector_size: int

    @abc.abstractmethod
    async def embed_text(self, text: str) -> EmbeddingResult:
        raise NotImplementedError

    @abc.abstractmethod
    async def embed_texts(self, texts: list[str]) -> EmbeddingBatchResult:
        raise NotImplementedError
