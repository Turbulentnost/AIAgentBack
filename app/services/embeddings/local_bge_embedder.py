from __future__ import annotations

import hashlib
import threading
import time
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.services.document_processing.concurrency import run_blocking_document_task
from app.services.embeddings.base import BaseEmbedder
from app.services.embeddings.exceptions import (
    EmbeddingConfigurationError,
    EmbeddingProviderUnavailableError,
    EmbeddingVectorSizeMismatchError,
)
from app.services.embeddings.schemas import EmbeddingBatchResult, EmbeddingResult

logger = get_logger(__name__)


class LocalBgeEmbedder(BaseEmbedder):
    provider = "local"

    def __init__(
        self,
        *,
        model_name: str | None = None,
        vector_size: int | None = None,
        device: str | None = None,
        allow_cpu_fallback: bool | None = None,
    ) -> None:
        self.model_name = model_name or settings.EMBEDDINGS_MODEL
        self.vector_size = vector_size or settings.EMBEDDINGS_VECTOR_SIZE
        self.requested_device = device or settings.EMBEDDINGS_DEVICE
        self.allow_cpu_fallback = (
            settings.EMBEDDINGS_ALLOW_CPU_FALLBACK
            if allow_cpu_fallback is None
            else allow_cpu_fallback
        )
        self._device: str | None = None
        self._model: Any | None = None
        self._model_lock = threading.Lock()

    @property
    def device(self) -> str:
        if self._device is None:
            self._device = self._resolve_device()
        return self._device

    async def embed_text(self, text: str) -> EmbeddingResult:
        batch = await self.embed_texts([text])
        return batch.items[0]

    async def embed_texts(self, texts: list[str]) -> EmbeddingBatchResult:
        started = time.perf_counter()
        vectors = await run_blocking_document_task(self._encode_texts, texts)
        items = [
            EmbeddingResult(
                text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                vector=vector,
                provider=self.provider,
                model=self.model_name,
                vector_size=len(vector),
                status="completed",
                metadata={"device": self.device},
            )
            for text, vector in zip(texts, vectors, strict=True)
        ]
        for item in items:
            self._validate_vector_size(item.vector)
        return EmbeddingBatchResult(
            items=items,
            provider=self.provider,
            model=self.model_name,
            vector_size=self.vector_size,
            total=len(items),
            failed_count=0,
            metadata={
                "device": self.device,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )

    def _encode_texts(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        try:
            vectors = model.encode(
                texts,
                batch_size=settings.EMBEDDINGS_BATCH_SIZE,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            raise EmbeddingProviderUnavailableError(
                f"Локальная embedding-модель недоступна: {exc}"
            ) from exc

        return [self._vector_to_list(vector) for vector in vectors]

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            raise EmbeddingProviderUnavailableError(
                "Не установлена зависимость sentence-transformers для локального embedder-а"
            ) from exc

        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                self._model = SentenceTransformer(self.model_name, device=self.device)
            except Exception as exc:
                raise EmbeddingProviderUnavailableError(
                    f"Не удалось загрузить embedding-модель {self.model_name}: {exc}"
                ) from exc
        return self._model

    def _resolve_device(self) -> str:
        if self.requested_device != "cuda":
            return self.requested_device

        try:
            import torch
        except Exception as exc:
            if self._can_fallback_to_cpu():
                logger.warning(
                    "embeddings.cuda_check.torch_unavailable_cpu_fallback",
                    provider=self.provider,
                    model=self.model_name,
                    error=str(exc),
                )
                return "cpu"
            raise EmbeddingConfigurationError(
                "EMBEDDINGS_DEVICE=cuda, но torch недоступен для проверки CUDA"
            ) from exc

        if torch.cuda.is_available():
            return "cuda"

        if self._can_fallback_to_cpu():
            logger.warning(
                "embeddings.cuda_unavailable_cpu_fallback",
                provider=self.provider,
                model=self.model_name,
            )
            return "cpu"

        raise EmbeddingConfigurationError(
            "EMBEDDINGS_DEVICE=cuda, но CUDA недоступна. "
            "Для dev можно включить EMBEDDINGS_ALLOW_CPU_FALLBACK=true."
        )

    def _can_fallback_to_cpu(self) -> bool:
        return settings.ENVIRONMENT == "dev" and self.allow_cpu_fallback

    def _validate_vector_size(self, vector: list[float]) -> None:
        if len(vector) != self.vector_size:
            raise EmbeddingVectorSizeMismatchError(
                f"Embedding vector size {len(vector)} не совпадает с "
                f"EMBEDDINGS_VECTOR_SIZE={self.vector_size}"
            )

    def _vector_to_list(self, vector: Any) -> list[float]:
        if hasattr(vector, "tolist"):
            return [float(item) for item in vector.tolist()]
        return [float(item) for item in vector]
