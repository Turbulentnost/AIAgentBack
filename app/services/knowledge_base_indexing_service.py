from __future__ import annotations

import uuid
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.integrations.qdrant import QdrantPoint, qdrant_client
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.enums import (
    KnowledgeBaseIndexErrorType,
    KnowledgeBaseIndexJobStatus,
    KnowledgeBaseIndexJobType,
    KnowledgeBaseSourceStatus,
    KnowledgeBaseStatus,
)
from app.models.knowledge_base import (
    KnowledgeBase,
    KnowledgeBaseChunk,
    KnowledgeBaseIndexingError,
    KnowledgeBaseIndexingJob,
    KnowledgeBaseSource,
)
from app.services.embeddings import EmbeddingService, embedding_service


class KnowledgeBaseIndexingError(RuntimeError):
    pass


class KnowledgeBaseIndexingService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        embedder: EmbeddingService | None = None,
    ) -> None:
        self.db = db
        self.embedding_service = embedder or embedding_service

    async def create_job(
        self,
        knowledge_base_id: uuid.UUID,
        *,
        job_type: KnowledgeBaseIndexJobType = KnowledgeBaseIndexJobType.FULL,
        started_by_user_id: uuid.UUID | None = None,
        target_source_id: uuid.UUID | None = None,
        target_chunk_id: uuid.UUID | None = None,
    ) -> KnowledgeBaseIndexingJob:
        kb = await self.db.get(KnowledgeBase, knowledge_base_id)
        if kb is None:
            raise KnowledgeBaseIndexingError("База знаний не найдена")

        job = KnowledgeBaseIndexingJob(
            knowledge_base_id=knowledge_base_id,
            job_type=job_type,
            status=KnowledgeBaseIndexJobStatus.QUEUED,
            target_source_id=target_source_id,
            target_chunk_id=target_chunk_id,
            started_by_user_id=started_by_user_id,
            embedding_model=kb.embedding_model or settings.EMBEDDINGS_MODEL,
            vector_store=kb.vector_store,
            qdrant_collection=kb.qdrant_collection,
        )
        self.db.add(job)
        await self.db.flush()
        return job

    async def run_job(self, job_id: uuid.UUID) -> dict[str, Any]:
        job = await self.db.get(KnowledgeBaseIndexingJob, job_id)
        if job is None:
            raise KnowledgeBaseIndexingError("Задание индексации не найдено")

        if job.job_type == KnowledgeBaseIndexJobType.SOURCE and job.target_source_id is not None:
            return await self.index_source(job.knowledge_base_id, job.target_source_id, job=job)
        return await self.index_knowledge_base(job.knowledge_base_id, job=job)

    async def index_knowledge_base(
        self,
        knowledge_base_id: uuid.UUID,
        *,
        job: KnowledgeBaseIndexingJob | None = None,
        started_by_user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        kb = await self._load_kb(knowledge_base_id)
        if job is None:
            job = await self.create_job(
                knowledge_base_id,
                job_type=KnowledgeBaseIndexJobType.FULL,
                started_by_user_id=started_by_user_id,
            )

        await self._mark_job_running(job, kb)
        kb.status = KnowledgeBaseStatus.UPDATING
        started = perf_counter()

        processed_sources = 0
        created_fragments = 0
        updated_fragments = 0
        try:
            await qdrant_client.ensure_collection(
                collection=kb.qdrant_collection,
                vector_size=settings.EMBEDDINGS_VECTOR_SIZE,
            )
            for source in kb.sources:
                result = await self._index_loaded_source(kb, source)
                processed_sources += 1
                created_fragments += result["created_fragments_count"]
                updated_fragments += result["updated_fragments_count"]

            await self._refresh_aggregates(kb)
            kb.status = KnowledgeBaseStatus.READY
            kb.last_indexed_at = _now()
            await self._mark_job_completed(
                job,
                started,
                processed_sources_count=processed_sources,
                created_fragments_count=created_fragments,
                updated_fragments_count=updated_fragments,
            )
            return self._job_result(job)
        except Exception as exc:
            await self._record_error(job, KnowledgeBaseIndexErrorType.QDRANT_WRITE_FAILED, str(exc))
            kb.status = KnowledgeBaseStatus.ERROR
            await self._mark_job_failed(job, started)
            raise

    async def index_source(
        self,
        knowledge_base_id: uuid.UUID,
        source_id: uuid.UUID,
        *,
        job: KnowledgeBaseIndexingJob | None = None,
        started_by_user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        kb = await self._load_kb(knowledge_base_id)
        source = next((item for item in kb.sources if item.id == source_id), None)
        if source is None:
            raise KnowledgeBaseIndexingError("Источник базы знаний не найден")
        if job is None:
            job = await self.create_job(
                knowledge_base_id,
                job_type=KnowledgeBaseIndexJobType.SOURCE,
                started_by_user_id=started_by_user_id,
                target_source_id=source_id,
            )

        await self._mark_job_running(job, kb)
        kb.status = KnowledgeBaseStatus.UPDATING
        started = perf_counter()
        try:
            result = await self._index_loaded_source(kb, source)
            await self._refresh_aggregates(kb)
            kb.status = KnowledgeBaseStatus.READY
            kb.last_indexed_at = _now()
            await self._mark_job_completed(
                job,
                started,
                processed_sources_count=1,
                created_fragments_count=result["created_fragments_count"],
                updated_fragments_count=result["updated_fragments_count"],
            )
            return self._job_result(job)
        except Exception as exc:
            await self._record_error(job, KnowledgeBaseIndexErrorType.QDRANT_WRITE_FAILED, str(exc), source_id=source.id)
            source.processing_status = KnowledgeBaseSourceStatus.ERROR
            kb.status = KnowledgeBaseStatus.ERROR
            await self._mark_job_failed(job, started)
            raise

    async def _index_loaded_source(self, kb: KnowledgeBase, source: KnowledgeBaseSource) -> dict[str, int]:
        document = await self.db.get(Document, source.document_id)
        version = await self.db.get(DocumentVersion, source.document_version_id)
        if document is None or version is None:
            raise KnowledgeBaseIndexingError("Документ-источник или версия не найдены")

        chunks = await self._load_document_chunks(source.document_version_id)
        if not chunks:
            raise KnowledgeBaseIndexingError("У источника нет фрагментов для индексации")

        await qdrant_client.delete_by_filter(
            {"knowledge_base_id": str(kb.id), "knowledge_base_source_id": str(source.id)},
            collection=kb.qdrant_collection,
        )

        kb_chunks_by_document_chunk = await self._load_kb_chunks(kb.id, [chunk.id for chunk in chunks])
        created = 0
        updated = 0
        kb_chunks: list[KnowledgeBaseChunk] = []
        for chunk in chunks:
            kb_chunk = kb_chunks_by_document_chunk.get(chunk.id)
            metadata = chunk.metadata_ or chunk.chunk_metadata or {}
            if kb_chunk is None:
                kb_chunk = KnowledgeBaseChunk(
                    knowledge_base_id=kb.id,
                    source_id=source.id,
                    document_chunk_id=chunk.id,
                    clause_number=metadata.get("clause") or metadata.get("clause_number"),
                    fragment_type=metadata.get("fragment_type") or "text",
                    access_snapshot=self._access_snapshot(document, metadata),
                )
                self.db.add(kb_chunk)
                created += 1
            else:
                kb_chunk.is_excluded_from_search = False
                kb_chunk.embedding_status = "pending"
                kb_chunk.access_snapshot = self._access_snapshot(document, metadata)
                updated += 1
            kb_chunks.append(kb_chunk)

        await self.db.flush()

        texts = [chunk.text or chunk.content for chunk in chunks]
        embeddings = await self.embedding_service.embed_texts(texts)
        points = [
            QdrantPoint(
                id=str(kb_chunk.id),
                vector=embedding.vector,
                payload=self._payload(kb, source, document, version, chunk, kb_chunk),
            )
            for chunk, kb_chunk, embedding in zip(chunks, kb_chunks, embeddings.items, strict=True)
            if not kb_chunk.is_excluded_from_search
        ]
        await qdrant_client.upsert_points(
            points,
            collection=kb.qdrant_collection,
            vector_size=settings.EMBEDDINGS_VECTOR_SIZE,
        )

        now = _now()
        for chunk, kb_chunk, embedding in zip(chunks, kb_chunks, embeddings.items, strict=True):
            kb_chunk.embedding_status = "indexed"
            kb_chunk.indexed_at = now
            chunk.embedding_model = embedding.model
            chunk.qdrant_collection = kb.qdrant_collection
            chunk.qdrant_point_id = str(kb_chunk.id)
            chunk.vector_id = str(kb_chunk.id)
            chunk.is_indexed = True

        source.processing_status = KnowledgeBaseSourceStatus.READY
        source.last_indexed_at = now
        source.fragments_count = len(kb_chunks)
        version.is_indexed = True
        version.qdrant_collection = kb.qdrant_collection
        version.qdrant_points_count = len(points)
        document.is_indexed = True
        await self.db.flush()
        return {"created_fragments_count": created, "updated_fragments_count": updated}

    async def _load_kb(self, knowledge_base_id: uuid.UUID) -> KnowledgeBase:
        result = await self.db.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.id == knowledge_base_id)
            .options(selectinload(KnowledgeBase.sources))
        )
        kb = result.scalar_one_or_none()
        if kb is None:
            raise KnowledgeBaseIndexingError("База знаний не найдена")
        return kb

    async def _load_document_chunks(self, document_version_id: uuid.UUID) -> list[DocumentChunk]:
        result = await self.db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_version_id == document_version_id)
            .order_by(DocumentChunk.chunk_index.asc())
        )
        return [chunk for chunk in result.scalars().all() if chunk.text or chunk.content]

    async def _load_kb_chunks(
        self,
        knowledge_base_id: uuid.UUID,
        document_chunk_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, KnowledgeBaseChunk]:
        if not document_chunk_ids:
            return {}
        result = await self.db.execute(
            select(KnowledgeBaseChunk).where(
                KnowledgeBaseChunk.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseChunk.document_chunk_id.in_(document_chunk_ids),
            )
        )
        return {item.document_chunk_id: item for item in result.scalars().all()}

    async def _refresh_aggregates(self, kb: KnowledgeBase) -> None:
        sources_count = await self.db.scalar(
            select(func.count(KnowledgeBaseSource.id)).where(KnowledgeBaseSource.knowledge_base_id == kb.id)
        )
        fragments_count = await self.db.scalar(
            select(func.count(KnowledgeBaseChunk.id)).where(
                KnowledgeBaseChunk.knowledge_base_id == kb.id,
                KnowledgeBaseChunk.is_excluded_from_search.is_(False),
            )
        )
        storage_bytes = await self.db.scalar(
            select(func.coalesce(func.sum(KnowledgeBaseSource.file_size), 0)).where(
                KnowledgeBaseSource.knowledge_base_id == kb.id
            )
        )
        kb.sources_count = int(sources_count or 0)
        kb.fragments_count = int(fragments_count or 0)
        kb.storage_bytes = int(storage_bytes or 0)
        await self.db.flush()

    def _payload(
        self,
        kb: KnowledgeBase,
        source: KnowledgeBaseSource,
        document: Document,
        version: DocumentVersion,
        chunk: DocumentChunk,
        kb_chunk: KnowledgeBaseChunk,
    ) -> dict[str, Any]:
        metadata = chunk.metadata_ or chunk.chunk_metadata or {}
        return {
            "knowledge_base_id": str(kb.id),
            "knowledge_base_source_id": str(source.id),
            "knowledge_base_chunk_id": str(kb_chunk.id),
            "document_id": str(document.id),
            "document_version_id": str(version.id),
            "chunk_id": str(chunk.id),
            "document_title": document.title,
            "document_type": document.document_type.value,
            "department_id": str(document.department_id) if document.department_id else None,
            "page_number": chunk.page_number,
            "section_title": chunk.section_title,
            "clause_number": kb_chunk.clause_number,
            "fragment_type": kb_chunk.fragment_type,
            "is_active": version.is_current and not kb_chunk.is_excluded_from_search,
            "access_scope": metadata.get("access_scope", "department"),
            "document_access_hash": self._document_access_hash(document),
        }

    def _access_snapshot(self, document: Document, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "document_id": str(document.id),
            "department_id": str(document.department_id) if document.department_id else None,
            "access_scope": metadata.get("access_scope", "department"),
            "document_access_hash": self._document_access_hash(document),
        }

    def _document_access_hash(self, document: Document) -> str:
        return f"{document.department_id}:{document.updated_at.isoformat() if document.updated_at else ''}"

    async def _mark_job_running(self, job: KnowledgeBaseIndexingJob, kb: KnowledgeBase) -> None:
        job.status = KnowledgeBaseIndexJobStatus.RUNNING
        job.started_at = _now()
        job.embedding_model = kb.embedding_model or settings.EMBEDDINGS_MODEL
        job.qdrant_collection = kb.qdrant_collection
        await self.db.flush()

    async def _mark_job_completed(
        self,
        job: KnowledgeBaseIndexingJob,
        started: float,
        *,
        processed_sources_count: int,
        created_fragments_count: int,
        updated_fragments_count: int,
    ) -> None:
        job.status = KnowledgeBaseIndexJobStatus.COMPLETED if job.errors_count == 0 else KnowledgeBaseIndexJobStatus.PARTIAL
        job.finished_at = _now()
        job.duration_ms = int((perf_counter() - started) * 1000)
        job.processed_sources_count = processed_sources_count
        job.created_fragments_count = created_fragments_count
        job.updated_fragments_count = updated_fragments_count
        await self.db.flush()

    async def _mark_job_failed(self, job: KnowledgeBaseIndexingJob, started: float) -> None:
        job.status = KnowledgeBaseIndexJobStatus.FAILED
        job.finished_at = _now()
        job.duration_ms = int((perf_counter() - started) * 1000)
        await self.db.flush()

    async def _record_error(
        self,
        job: KnowledgeBaseIndexingJob,
        error_type: KnowledgeBaseIndexErrorType,
        technical_message: str,
        *,
        source_id: uuid.UUID | None = None,
    ) -> None:
        job.errors_count += 1
        self.db.add(
            KnowledgeBaseIndexingError(
                job_id=job.id,
                knowledge_base_id=job.knowledge_base_id,
                source_id=source_id,
                error_type=error_type,
                technical_message=technical_message,
                user_message="Индексация базы знаний завершилась с ошибкой.",
                recommended_action="Проверьте источник и запустите повторную обработку.",
            )
        )
        await self.db.flush()

    def _job_result(self, job: KnowledgeBaseIndexingJob) -> dict[str, Any]:
        return {
            "job_id": str(job.id),
            "knowledge_base_id": str(job.knowledge_base_id),
            "status": job.status.value,
            "processed_sources_count": job.processed_sources_count,
            "created_fragments_count": job.created_fragments_count,
            "updated_fragments_count": job.updated_fragments_count,
            "errors_count": job.errors_count,
            "embedding_model": job.embedding_model,
            "qdrant_collection": job.qdrant_collection,
            "duration_ms": job.duration_ms,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)
