from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.qdrant import QdrantPoint, qdrant_client
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.enums import DocumentProcessingStatus
from app.services.embeddings import EmbeddingService, embedding_service


class QdrantIndexingError(RuntimeError):
    pass


class QdrantIndexingService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        embedder: EmbeddingService | None = None,
    ) -> None:
        self.db = db
        self.embedding_service = embedder or embedding_service

    async def index_document_version(self, document_version_id: uuid.UUID) -> dict[str, Any]:
        document_version = await self.db.get(DocumentVersion, document_version_id)
        if document_version is None:
            raise QdrantIndexingError("Версия документа не найдена")

        document = await self.db.get(Document, document_version.document_id)
        if document is None:
            raise QdrantIndexingError("Документ не найден")

        chunks = await self._load_chunks(document_version.id)
        if not chunks:
            raise QdrantIndexingError("Нет DocumentChunk для индексации")

        await qdrant_client.delete_by_document_version(str(document_version.id))
        texts = [chunk.text or chunk.content for chunk in chunks]
        embeddings = await self.embedding_service.embed_texts(texts)

        points = [
            QdrantPoint(
                id=str(chunk.id),
                vector=embedding.vector,
                payload=self._payload(document, document_version, chunk),
            )
            for chunk, embedding in zip(chunks, embeddings.items, strict=True)
        ]
        await qdrant_client.upsert_points(
            points,
            collection=settings.QDRANT_COLLECTION,
            vector_size=settings.EMBEDDINGS_VECTOR_SIZE,
        )

        for chunk, embedding in zip(chunks, embeddings.items, strict=True):
            chunk.embedding_model = embedding.model
            chunk.qdrant_collection = settings.QDRANT_COLLECTION
            chunk.qdrant_point_id = str(chunk.id)
            chunk.vector_id = str(chunk.id)
            chunk.is_indexed = True

        document.is_indexed = True
        document.processing_status = DocumentProcessingStatus.INDEXED
        document_version.is_indexed = True
        document_version.processing_status = DocumentProcessingStatus.INDEXED
        document_version.qdrant_collection = settings.QDRANT_COLLECTION
        document_version.qdrant_points_count = len(points)
        await self.db.flush()

        return {
            "document_id": str(document.id),
            "document_version_id": str(document_version.id),
            "collection": settings.QDRANT_COLLECTION,
            "points_count": len(points),
            "embedding_model": embeddings.model,
            "vector_size": embeddings.vector_size,
        }

    async def index_document(self, document_id: uuid.UUID) -> dict[str, Any]:
        result = await self.db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.is_current.desc(), DocumentVersion.version_number.desc())
            .limit(1)
        )
        document_version = result.scalar_one_or_none()
        if document_version is None:
            raise QdrantIndexingError("У документа нет версий для индексации")
        return await self.index_document_version(document_version.id)

    async def _load_chunks(self, document_version_id: uuid.UUID) -> list[DocumentChunk]:
        result = await self.db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_version_id == document_version_id)
            .order_by(DocumentChunk.chunk_index.asc())
        )
        return [chunk for chunk in result.scalars().all() if (chunk.text or chunk.content)]

    def _payload(
        self,
        document: Document,
        document_version: DocumentVersion,
        chunk: DocumentChunk,
    ) -> dict[str, Any]:
        metadata = chunk.metadata_ or chunk.chunk_metadata or {}
        return {
            "document_id": str(document.id),
            "document_version_id": str(document_version.id),
            "chunk_id": str(chunk.id),
            "document_title": document.title,
            "document_type": document.document_type.value,
            "department_id": str(document.department_id) if document.department_id else None,
            "page_number": chunk.page_number,
            "section_title": chunk.section_title,
            "is_active": document_version.is_current,
            "access_scope": metadata.get("access_scope", "department"),
        }
