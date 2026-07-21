"""RAG-контур базы знаний (гибридный поиск) для узлов агентов.

Боевой режим: если привязан :class:`AgentRuntime` с ретривером, поиск идёт в
``HybridRetriever.retrieve(...)`` (Qdrant + BGE-M3, при наличии БД — ещё и FTS)
через мост sync→async. Резервный режим: детерминированный мок-фрагмент.

Замечание о честности покрытия: без ``AsyncSession`` и объекта ``KnowledgeBase``
боевой ретривер деградирует до чистого вектор-поиска по коллекции по умолчанию
(без RBAC-фильтрации и без части метаданных источника из Postgres).
"""

from __future__ import annotations

from typing import Any


def _mock(collection: str, query: str, full_text_terms: list[str] | None) -> list[dict[str, Any]]:
    return [
        {
            "collection": collection,
            "query": query,
            "full_text_terms": full_text_terms or [],
            "chunk": "",
            "document": "СТО-28-020",
            "clause": "",
            "version": "07",
            "score": 0.0,
            "_mock": True,
        }
    ]


def _to_fragment(hit: dict[str, Any], collection: str, query: str) -> dict[str, Any]:
    payload = hit.get("payload") if isinstance(hit.get("payload"), dict) else hit
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return {
        "collection": collection,
        "query": query,
        "chunk": payload.get("content") or payload.get("chunk") or hit.get("content") or "",
        "document": (
            payload.get("document_title")
            or payload.get("document")
            or meta.get("document")
            or ""
        ),
        "document_id": payload.get("document_id") or hit.get("document_id"),
        "clause": payload.get("clause_number") or meta.get("clause") or "",
        "version": payload.get("version") or meta.get("version") or "",
        "page": payload.get("page_number") or meta.get("page"),
        "score": hit.get("score") or payload.get("score") or 0.0,
        "_real": True,
    }


def hybrid_search(
    query: str,
    *,
    collection: str = "regulations",
    top_k: int = 5,
    full_text_terms: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Гибридный поиск в базе знаний. Боевой ретривер при наличии runtime, иначе мок."""

    try:
        from app.agents.omto_role_agents.runtime_context import current_runtime, run_async
    except Exception:  # noqa: BLE001
        return _mock(collection, query, full_text_terms)

    runtime = current_runtime()
    if runtime is None or runtime.retriever is None:
        return _mock(collection, query, full_text_terms)

    try:
        hits = run_async(runtime.retriever.retrieve(query, top_k=top_k))
    except Exception as exc:  # noqa: BLE001 — сбой поиска не роняет граф
        return [
            {
                "collection": collection,
                "query": query,
                "chunk": "",
                "unavailable": True,
                "_error": str(exc),
            }
        ]

    fragments = [
        _to_fragment(hit, collection, query)
        for hit in hits
        if isinstance(hit, dict)
    ]
    return fragments[:top_k] if fragments else _mock(collection, query, full_text_terms)
