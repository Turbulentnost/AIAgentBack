from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import replace

from app.core.config import settings
from app.core.logging import get_logger
from app.services.embeddings.base import BaseEmbedder
from app.services.embeddings.exceptions import (
    EmbeddingBatchError,
    EmbeddingConfigurationError,
    EmbeddingVectorSizeMismatchError,
    EmptyTextEmbeddingError,
    TextTooLongEmbeddingError,
)
from app.services.embeddings.local_bge_embedder import LocalBgeEmbedder
from app.services.embeddings.schemas import EmbeddingBatchResult, EmbeddingResult

logger = get_logger(__name__)


class EmbeddingService:
    def __init__(self, embedder: BaseEmbedder | None = None) -> None:
        self._embedder = embedder

    @property
    def embedder(self) -> BaseEmbedder:
        if self._embedder is None:
            self._embedder = self._build_embedder()
        return self._embedder

    async def embed_text(self, text: str) -> EmbeddingResult:
        batch = await self.embed_texts([text])
        return batch.items[0]

    async def embed_texts(self, texts: list[str]) -> EmbeddingBatchResult:
        started = time.perf_counter()
        normalized_texts = [self.normalize_text(text) for text in texts]
        for text in normalized_texts:
            self._validate_text(text)

        batches = self._split_batches(normalized_texts, settings.EMBEDDINGS_BATCH_SIZE)
        logger.info(
            "embeddings.batch.started",
            provider=self.embedder.provider,
            model=self.embedder.model_name,
            total=len(normalized_texts),
            batch_size=settings.EMBEDDINGS_BATCH_SIZE,
            batches=len(batches),
            vector_size=settings.EMBEDDINGS_VECTOR_SIZE,
            device=getattr(self.embedder, "requested_device", None),
        )

        try:
            batch_results = await asyncio.gather(
                *(self.embedder.embed_texts(batch) for batch in batches)
            )
        except Exception as exc:
            raise EmbeddingBatchError(f"Не удалось сформировать embeddings batch: {exc}") from exc

        items: list[EmbeddingResult] = []
        for batch, batch_result in zip(batches, batch_results, strict=True):
            if len(batch_result.items) != len(batch):
                raise EmbeddingBatchError(
                    f"Provider вернул {len(batch_result.items)} embeddings для batch из {len(batch)} texts"
                )
            for text, item in zip(batch, batch_result.items, strict=True):
                self._validate_vector_size(item.vector)
                items.append(
                    replace(
                        item,
                        text_hash=self._hash_text(text),
                        vector_size=len(item.vector),
                        metadata={
                            **item.metadata,
                            "normalized": True,
                            "original_provider": item.provider,
                        },
                    )
                )

        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "embeddings.batch.completed",
            provider=self.embedder.provider,
            model=self.embedder.model_name,
            total=len(items),
            failed_count=0,
            batch_size=settings.EMBEDDINGS_BATCH_SIZE,
            duration_ms=duration_ms,
            vector_size=settings.EMBEDDINGS_VECTOR_SIZE,
            device=getattr(self.embedder, "device", None),
        )
        return EmbeddingBatchResult(
            items=items,
            provider=self.embedder.provider,
            model=self.embedder.model_name,
            vector_size=settings.EMBEDDINGS_VECTOR_SIZE,
            total=len(items),
            failed_count=0,
            metadata={
                "batch_size": settings.EMBEDDINGS_BATCH_SIZE,
                "batches": len(batches),
                "duration_ms": duration_ms,
            },
        )

    def normalize_text(self, text: str) -> str:
        value = text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
        lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in value.split("\n")]
        return "\n".join(line for line in lines if line).strip()

    def _build_embedder(self) -> BaseEmbedder:
        provider = settings.EMBEDDINGS_PROVIDER.lower()
        if provider == "local":
            return LocalBgeEmbedder()
        raise EmbeddingConfigurationError(
            "Поддерживается только EMBEDDINGS_PROVIDER=local с моделью BAAI/bge-m3. "
            f"Получено EMBEDDINGS_PROVIDER={provider}."
        )

    def _split_batches(self, texts: list[str], batch_size: int) -> list[list[str]]:
        size = max(1, batch_size)
        return [texts[index : index + size] for index in range(0, len(texts), size)]

    def _validate_text(self, text: str) -> None:
        if not text:
            raise EmptyTextEmbeddingError("Нельзя построить embedding для пустого текста")
        if len(text) > settings.EMBEDDINGS_MAX_TEXT_LENGTH:
            raise TextTooLongEmbeddingError(
                f"Текст для embedding длиннее EMBEDDINGS_MAX_TEXT_LENGTH="
                f"{settings.EMBEDDINGS_MAX_TEXT_LENGTH}"
            )

    def _validate_vector_size(self, vector: list[float]) -> None:
        if len(vector) != settings.EMBEDDINGS_VECTOR_SIZE:
            raise EmbeddingVectorSizeMismatchError(
                f"Embedding vector size {len(vector)} не совпадает с "
                f"EMBEDDINGS_VECTOR_SIZE={settings.EMBEDDINGS_VECTOR_SIZE}"
            )

    def _hash_text(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


embedding_service = EmbeddingService()
