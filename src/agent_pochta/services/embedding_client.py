"""HTTP-клиент эмбеддингов (OpenAI-compatible /v1/embeddings, BGE на 192.168.1.157)."""

from __future__ import annotations

import logging
from typing import Sequence

import httpx

from agent_pochta.config import Settings, get_settings

logger = logging.getLogger(__name__)


class EmbeddingClientError(RuntimeError):
    pass


def _embeddings_url(settings: Settings) -> str:
    base = (settings.embedding_base_url or "").rstrip("/")
    if not base:
        raise EmbeddingClientError("EMBEDDING_BASE_URL is not configured")
    if base.endswith("/embeddings"):
        return base
    if base.endswith("/v1"):
        return f"{base}/embeddings"
    return f"{base}/v1/embeddings"


def embed_texts(
    texts: Sequence[str],
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> list[list[float]]:
    """Batch-embed texts via OpenAI-compatible API (TEI, Infinity, LM Studio embeddings)."""
    settings = settings or get_settings()
    cleaned = [text.strip() for text in texts if text and text.strip()]
    if not cleaned:
        return []

    url = _embeddings_url(settings)
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.embedding_api_key:
        headers["Authorization"] = f"Bearer {settings.embedding_api_key}"

    payload = {
        "model": settings.embedding_model,
        "input": cleaned,
    }
    timeout = settings.embedding_timeout_sec
    own_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        items = data.get("data") or []
        if len(items) != len(cleaned):
            raise EmbeddingClientError(
                f"embedding count mismatch: sent {len(cleaned)}, got {len(items)}"
            )
        vectors: list[list[float]] = []
        for item in sorted(items, key=lambda row: row.get("index", 0)):
            vector = item.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise EmbeddingClientError("empty embedding vector in response")
            if len(vector) != settings.embedding_vector_size:
                raise EmbeddingClientError(
                    f"vector size {len(vector)} != configured {settings.embedding_vector_size}"
                )
            vectors.append([float(v) for v in vector])
        return vectors
    except httpx.HTTPError as exc:
        logger.warning("embedding_request_failed url=%s error=%s", url, exc)
        raise EmbeddingClientError(str(exc)) from exc
    finally:
        if own_client:
            http.close()


def check_embedding_service(*, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not settings.embedding_base_url:
        return False
    try:
        vectors = embed_texts(["ping"], settings=settings)
        return bool(vectors)
    except EmbeddingClientError:
        return False
