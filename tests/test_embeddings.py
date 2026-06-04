from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.embeddings.base import BaseEmbedder
from app.services.embeddings.embedding_service import EmbeddingService
from app.services.embeddings.exceptions import (
    EmbeddingConfigurationError,
    EmbeddingVectorSizeMismatchError,
    EmptyTextEmbeddingError,
)
from app.services.embeddings.local_bge_embedder import LocalBgeEmbedder
from app.services.embeddings.openai_embedder import OpenAIEmbedder
from app.services.embeddings.schemas import EmbeddingBatchResult, EmbeddingResult


class FakeEmbedder(BaseEmbedder):
    provider = "local"
    model_name = "BAAI/bge-m3"

    def __init__(self, vector_size: int, wrong_size: bool = False) -> None:
        self.vector_size = vector_size
        self.wrong_size = wrong_size
        self.batch_sizes: list[int] = []

    async def embed_text(self, text: str) -> EmbeddingResult:
        batch = await self.embed_texts([text])
        return batch.items[0]

    async def embed_texts(self, texts: list[str]) -> EmbeddingBatchResult:
        self.batch_sizes.append(len(texts))
        size = self.vector_size - 1 if self.wrong_size else self.vector_size
        items = [
            EmbeddingResult(
                text_hash="",
                vector=[0.1] * size,
                provider=self.provider,
                model=self.model_name,
                vector_size=size,
                status="completed",
                metadata={},
            )
            for _text in texts
        ]
        return EmbeddingBatchResult(
            items=items,
            provider=self.provider,
            model=self.model_name,
            vector_size=size,
            total=len(items),
            failed_count=0,
            metadata={},
        )


@pytest.mark.asyncio
async def test_embed_text_returns_expected_vector_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EMBEDDINGS_VECTOR_SIZE", 3)
    service = EmbeddingService(FakeEmbedder(vector_size=3))

    result = await service.embed_text("СГ-ТК-Д-16 DN 50 PN 16")

    assert result.model == "BAAI/bge-m3"
    assert result.vector_size == 3
    assert len(result.vector) == 3


@pytest.mark.asyncio
async def test_embed_texts_processes_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EMBEDDINGS_VECTOR_SIZE", 3)
    monkeypatch.setattr(settings, "EMBEDDINGS_BATCH_SIZE", 2)
    embedder = FakeEmbedder(vector_size=3)
    service = EmbeddingService(embedder)

    result = await service.embed_texts(["one", "two", "three", "four", "five"])

    assert result.total == 5
    assert embedder.batch_sizes == [2, 2, 1]


@pytest.mark.asyncio
async def test_empty_text_raises_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EMBEDDINGS_VECTOR_SIZE", 3)
    service = EmbeddingService(FakeEmbedder(vector_size=3))

    with pytest.raises(EmptyTextEmbeddingError):
        await service.embed_text(" \n\t ")


def test_normalize_text_preserves_technical_designations() -> None:
    service = EmbeddingService(FakeEmbedder(vector_size=settings.EMBEDDINGS_VECTOR_SIZE))
    text = """
    СГ-ТК-Д-16   ГРАНД-6ТК
    DN 50    PN 16
    Qmax 10 м³/ч
    G6
    ТУ 4213-001-...
    ГОСТ 2939-63
    1:100
    0,6 МПа
    ±1,5 %
    """

    normalized = service.normalize_text(text)

    for value in [
        "СГ-ТК-Д-16",
        "ГРАНД-6ТК",
        "DN 50",
        "PN 16",
        "Qmax 10 м³/ч",
        "G6",
        "ТУ 4213-001-...",
        "ГОСТ 2939-63",
        "1:100",
        "0,6 МПа",
        "±1,5 %",
    ]:
        assert value in normalized


@pytest.mark.asyncio
async def test_vector_size_mismatch_raises_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EMBEDDINGS_VECTOR_SIZE", 3)
    service = EmbeddingService(FakeEmbedder(vector_size=3, wrong_size=True))

    with pytest.raises(EmbeddingVectorSizeMismatchError):
        await service.embed_text("valid text")


def test_cuda_unavailable_without_fallback_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    embedder = LocalBgeEmbedder(device="cuda", allow_cpu_fallback=False)

    with pytest.raises(EmbeddingConfigurationError):
        _ = embedder.device


def test_openai_embedder_is_disabled() -> None:
    with pytest.raises(EmbeddingConfigurationError):
        OpenAIEmbedder()
