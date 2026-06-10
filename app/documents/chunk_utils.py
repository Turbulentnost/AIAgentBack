from __future__ import annotations

from app.models.document import DocumentChunk


def chunk_embedding_text(chunk: DocumentChunk) -> str:
    """Текст для векторизации и полнотекстового индекса."""
    return (chunk.text or chunk.content or "").strip()


def chunk_display_text(chunk: DocumentChunk) -> str:
    """Текст для отображения пользователю (UI, поиск, карточки фрагментов)."""
    return (chunk.content or chunk.text or "").strip()
