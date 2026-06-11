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
    KnowledgeBaseChunkQualityStatus,
    KnowledgeBaseIndexErrorType,
    KnowledgeBaseIndexJobStatus,
    KnowledgeBaseIndexJobType,
    KnowledgeBaseSourcePrecheckStatus,
    KnowledgeBaseSourceStatus,
    KnowledgeBaseStatus,
)
from app.models.knowledge_base import (
    KnowledgeBase,
    KnowledgeBaseChunk,
    KnowledgeBaseIndexingError as KnowledgeBaseIndexingErrorModel,
    KnowledgeBaseIndexingJob,
    KnowledgeBaseSource,
)
from app.services.knowledge_base_indexing_events import (
    build_indexing_payload,
    is_indexing_active,
    publish_indexing_event,
)
from app.models.user import User
from app.services.embeddings import EmbeddingService, embedding_service
from app.services.knowledge_base_fts_service import KnowledgeBaseFtsService
from app.services.knowledge_base_precheck_service import KnowledgeBasePrecheckService


class KnowledgeBaseIndexingError(RuntimeError):
    pass


_SKIP_STATUSES = {
    KnowledgeBaseSourceStatus.ARCHIVED,
    KnowledgeBaseSourceStatus.EXCLUDED,
}


class KnowledgeBaseIndexingService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        embedder: EmbeddingService | None = None,
    ) -> None:
        self.db = db
        self.embedding_service = embedder or embedding_service
        self.precheck = KnowledgeBasePrecheckService(db)
        self.fts = KnowledgeBaseFtsService(db)

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
        if kb.deleted_at is not None:
            raise KnowledgeBaseIndexingError("База знаний удалена")

        metadata = kb.metadata_ or {}
        processing = metadata.get("processing_settings") or {}
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
            processing_params={
                "embedding_model": kb.embedding_model or settings.EMBEDDINGS_MODEL,
                "chunk_size": processing.get("chunkSize"),
                "chunk_overlap": processing.get("chunkOverlap"),
            },
        )
        self.db.add(job)
        await self.db.flush()
        kb = await self.db.get(KnowledgeBase, knowledge_base_id)
        if kb is not None:
            await self._emit_indexing_event(job, event="queued", kb=kb)
        return job

    async def mark_indexing_queued(self, knowledge_base_id: uuid.UUID) -> KnowledgeBase:
        kb = await self._load_kb(knowledge_base_id)
        metadata = kb.metadata_ or {}
        if kb.status not in {KnowledgeBaseStatus.PROCESSING, KnowledgeBaseStatus.UPDATING}:
            metadata["indexing_previous_status"] = kb.status.value
            kb.metadata_ = metadata
        kb.status = KnowledgeBaseStatus.PROCESSING
        for source in kb.sources:
            if source.processing_status not in _SKIP_STATUSES:
                source.processing_status = KnowledgeBaseSourceStatus.PROCESSING
        await self.db.flush()
        return kb

    async def has_active_full_job(self, knowledge_base_id: uuid.UUID) -> bool:
        """Есть ли уже активная полная (пере)индексация этой базы.

        Используется, чтобы не ставить в очередь дублирующие job'ы: вторая
        полная индексация сразу после первой лишь сбрасывает прогресс в UI.
        """
        result = await self.db.scalar(
            select(func.count(KnowledgeBaseIndexingJob.id)).where(
                KnowledgeBaseIndexingJob.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseIndexingJob.status.in_(
                    [KnowledgeBaseIndexJobStatus.QUEUED, KnowledgeBaseIndexJobStatus.RUNNING]
                ),
                KnowledgeBaseIndexingJob.job_type.in_(
                    [
                        KnowledgeBaseIndexJobType.FULL,
                        KnowledgeBaseIndexJobType.ACCESS_REINDEX,
                        KnowledgeBaseIndexJobType.EMBEDDINGS,
                    ]
                ),
            )
        )
        return bool(result)

    async def _has_other_active_jobs(self, knowledge_base_id: uuid.UUID, current_job_id: uuid.UUID) -> bool:
        result = await self.db.scalar(
            select(func.count(KnowledgeBaseIndexingJob.id)).where(
                KnowledgeBaseIndexingJob.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseIndexingJob.id != current_job_id,
                KnowledgeBaseIndexingJob.status.in_(
                    [KnowledgeBaseIndexJobStatus.QUEUED, KnowledgeBaseIndexJobStatus.RUNNING]
                ),
            )
        )
        return bool(result)

    async def supersede_stale_queued_jobs(self, knowledge_base_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(KnowledgeBaseIndexingJob).where(
                KnowledgeBaseIndexingJob.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseIndexingJob.status == KnowledgeBaseIndexJobStatus.QUEUED,
                KnowledgeBaseIndexingJob.started_at.is_(None),
            )
        )
        superseded = 0
        now = _now()
        for job in result.scalars():
            job.status = KnowledgeBaseIndexJobStatus.FAILED
            job.finished_at = now
            superseded += 1
        if superseded:
            await self.db.flush()
        return superseded

    async def request_cancel(
        self,
        knowledge_base_id: uuid.UUID,
        *,
        requested_by_user_id: uuid.UUID,
        reason: str | None = None,
    ) -> KnowledgeBaseIndexingJob:
        result = await self.db.execute(
            select(KnowledgeBaseIndexingJob)
            .where(
                KnowledgeBaseIndexingJob.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseIndexingJob.status.in_(
                    [KnowledgeBaseIndexJobStatus.QUEUED, KnowledgeBaseIndexJobStatus.RUNNING]
                ),
            )
            .order_by(KnowledgeBaseIndexingJob.created_at.desc())
            .limit(1)
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise KnowledgeBaseIndexingError("Активное задание индексации не найдено")
        job.cancel_requested = True
        job.cancel_requested_by_user_id = requested_by_user_id
        job.cancel_requested_at = _now()
        job.cancel_reason = reason or "Остановка по запросу пользователя"
        await self._set_job_stage(job, "stopping")
        if job.status == KnowledgeBaseIndexJobStatus.QUEUED:
            kb = await self._load_kb(knowledge_base_id)
            elapsed = (_now() - job.started_at).total_seconds() if job.started_at else 0.0
            started = perf_counter() - max(0.0, elapsed)
            await self._mark_job_cancelled(job, kb, started)
        await self.db.flush()
        return job

    async def force_cancel(
        self,
        knowledge_base_id: uuid.UUID,
        *,
        requested_by_user_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> KnowledgeBaseIndexingJob | None:
        result = await self.db.execute(
            select(KnowledgeBaseIndexingJob)
            .where(
                KnowledgeBaseIndexingJob.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseIndexingJob.status.in_(
                    [KnowledgeBaseIndexJobStatus.QUEUED, KnowledgeBaseIndexJobStatus.RUNNING]
                ),
            )
            .order_by(KnowledgeBaseIndexingJob.created_at.desc())
            .limit(1)
        )
        job = result.scalar_one_or_none()
        if job is None:
            kb = await self._load_kb(knowledge_base_id)
            if kb.status in {KnowledgeBaseStatus.PROCESSING, KnowledgeBaseStatus.UPDATING}:
                metadata = kb.metadata_ or {}
                previous_status = metadata.pop("indexing_previous_status", None)
                kb.metadata_ = metadata or None
                if previous_status and kb.fragments_count > 0:
                    try:
                        kb.status = KnowledgeBaseStatus(previous_status)
                    except ValueError:
                        kb.status = KnowledgeBaseStatus.READY if kb.fragments_count > 0 else KnowledgeBaseStatus.DRAFT
                elif kb.fragments_count > 0:
                    kb.status = KnowledgeBaseStatus.READY
                else:
                    kb.status = KnowledgeBaseStatus.DRAFT
                for source in kb.sources:
                    if source.processing_status == KnowledgeBaseSourceStatus.PROCESSING:
                        source.processing_status = KnowledgeBaseSourceStatus.READY_TO_INDEX
                await self.db.flush()
            return None
        job.cancel_requested = True
        if requested_by_user_id is not None:
            job.cancel_requested_by_user_id = requested_by_user_id
        job.cancel_requested_at = _now()
        job.cancel_reason = reason or "Принудительная остановка индексации"
        kb = await self._load_kb(knowledge_base_id)
        elapsed = (_now() - job.started_at).total_seconds() if job.started_at else 0.0
        started = perf_counter() - max(0.0, elapsed)
        await self._mark_job_cancelled(job, kb, started)
        await self.db.flush()
        return job

    async def precheck_all_sources(
        self,
        knowledge_base_id: uuid.UUID,
        *,
        user: User | None = None,
        job: KnowledgeBaseIndexingJob | None = None,
    ) -> list[KnowledgeBaseSource]:
        kb = await self._load_kb(knowledge_base_id)
        eligible: list[KnowledgeBaseSource] = []
        for source in kb.sources:
            if source.processing_status in _SKIP_STATUSES:
                continue
            result = await self.precheck.precheck_source(source, user=user)
            if result.passed:
                source.precheck_status = KnowledgeBaseSourcePrecheckStatus.PASSED
                if not result.needs_ocr:
                    source.processing_status = KnowledgeBaseSourceStatus.READY_TO_INDEX
                eligible.append(source)
            else:
                source.precheck_status = KnowledgeBaseSourcePrecheckStatus.FAILED
                source.precheck_notes = result.user_message
                source.processing_status = KnowledgeBaseSourceStatus.ERROR
                if job is not None and result.error_type is not None:
                    await self._record_error(
                        job,
                        result.error_type,
                        result.technical_message or result.user_message or "Precheck failed",
                        source_id=source.id,
                        user_message=result.user_message,
                        recommended_action=result.recommended_action,
                    )
        await self.db.flush()
        return eligible

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
        user: User | None = None,
    ) -> dict[str, Any]:
        kb = await self._load_kb(knowledge_base_id)
        if job is None:
            job = await self.create_job(
                knowledge_base_id,
                job_type=KnowledgeBaseIndexJobType.FULL,
                started_by_user_id=started_by_user_id,
            )

        await self._mark_job_running(job, kb)
        kb.status = KnowledgeBaseStatus.PROCESSING
        started = perf_counter()

        if await self._check_cancel_requested(job, kb, started):
            return self._job_result(job)

        await self._set_job_stage(job, "precheck")
        eligible = await self.precheck_all_sources(knowledge_base_id, user=user, job=job)
        job.total_sources_count = len(eligible)
        await self._update_job_progress(job)

        processed_sources = 0
        created_fragments = 0
        updated_fragments = 0
        total_chunks = 0
        embedded_chunks = 0
        qdrant_points = 0
        fulltext_chunks = 0

        try:
            await qdrant_client.ensure_collection(
                collection=kb.qdrant_collection,
                vector_size=settings.EMBEDDINGS_VECTOR_SIZE,
            )
            for source in eligible:
                if await self._check_cancel_requested(job, kb, started):
                    return self._job_result(job)
                try:
                    result = await self._index_loaded_source(kb, source, job=job)
                    processed_sources += 1
                    created_fragments += result["created_fragments_count"]
                    updated_fragments += result["updated_fragments_count"]
                    total_chunks += result["chunks_count"]
                    embedded_chunks += result["embedded_chunks_count"]
                    qdrant_points += result["qdrant_points_count"]
                    fulltext_chunks += result["fulltext_chunks_count"]
                    job.processed_sources_count = processed_sources
                    job.created_fragments_count = created_fragments
                    job.updated_fragments_count = updated_fragments
                    job.total_chunks_count = total_chunks
                    job.embedded_chunks_count = embedded_chunks
                    job.qdrant_points_count = qdrant_points
                    job.fulltext_chunks_count = fulltext_chunks
                    await self._update_job_progress(job)
                except Exception as exc:
                    await self._record_error(
                        job,
                        KnowledgeBaseIndexErrorType.QDRANT_WRITE_FAILED,
                        str(exc),
                        source_id=source.id,
                    )
                    source.processing_status = KnowledgeBaseSourceStatus.ERROR

            if await self._check_cancel_requested(job, kb, started):
                return self._job_result(job)

            await self._set_job_stage(job, "quality_control")
            await self._run_qc(kb, job)
            await self._refresh_aggregates(kb)
            kb.status = await self._resolve_final_kb_status_async(kb, job)
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
        user: User | None = None,
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
        kb.status = KnowledgeBaseStatus.PROCESSING
        started = perf_counter()
        job.total_sources_count = 1

        precheck = await self.precheck.precheck_source(source, user=user)
        if not precheck.passed:
            if precheck.error_type:
                await self._record_error(
                    job,
                    precheck.error_type,
                    precheck.technical_message or precheck.user_message or "",
                    source_id=source.id,
                    user_message=precheck.user_message,
                    recommended_action=precheck.recommended_action,
                )
            source.processing_status = KnowledgeBaseSourceStatus.ERROR
            await self._mark_job_failed(job, started)
            raise KnowledgeBaseIndexingError(precheck.user_message or "Источник не прошёл предпроверку")

        try:
            result = await self._index_loaded_source(kb, source, job=job)
            job.processed_sources_count = 1
            job.created_fragments_count = result["created_fragments_count"]
            job.updated_fragments_count = result["updated_fragments_count"]
            job.total_chunks_count = result["chunks_count"]
            job.embedded_chunks_count = result["embedded_chunks_count"]
            job.qdrant_points_count = result["qdrant_points_count"]
            job.fulltext_chunks_count = result["fulltext_chunks_count"]
            await self._run_qc(kb, job)
            await self._refresh_aggregates(kb)
            kb.status = await self._resolve_final_kb_status_async(kb, job)
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

    async def _index_loaded_source(
        self,
        kb: KnowledgeBase,
        source: KnowledgeBaseSource,
        *,
        job: KnowledgeBaseIndexingJob | None = None,
    ) -> dict[str, int]:
        document = await self.db.get(Document, source.document_id)
        version = await self.db.get(DocumentVersion, source.document_version_id)
        if document is None or version is None:
            raise KnowledgeBaseIndexingError("Документ-источник или версия не найдены")

        source.processing_status = KnowledgeBaseSourceStatus.PROCESSING
        if job is not None:
            await self._set_job_stage(job, "text_extraction")
            job.extracted_sources_count += 1
            await self._update_job_progress(job)

        await self._ensure_source_document_processed(document, version)
        # Парсер мог изменить документ/версию: после flush атрибуты с
        # server-side onupdate (updated_at) истекают, а их синхронное чтение
        # в async-сессии падает с MissingGreenlet. Обновляем явно.
        await self.db.refresh(document)
        await self.db.refresh(version)
        chunks = await self._load_document_chunks(source.document_version_id)
        if not chunks:
            raise KnowledgeBaseIndexingError("У источника нет фрагментов для индексации")

        if job is not None:
            await self._set_job_stage(job, "chunking")
            job.chunked_sources_count += 1
            await self._update_job_progress(job)

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
            quality = self._chunk_quality(chunk, metadata)
            if kb_chunk is None:
                kb_chunk = KnowledgeBaseChunk(
                    knowledge_base_id=kb.id,
                    source_id=source.id,
                    document_chunk_id=chunk.id,
                    clause_number=metadata.get("clause") or metadata.get("clause_number"),
                    fragment_type=metadata.get("fragment_type") or "text",
                    access_snapshot=self._access_snapshot(document, metadata),
                    quality_status=quality,
                )
                self.db.add(kb_chunk)
                created += 1
            else:
                kb_chunk.is_excluded_from_search = False
                kb_chunk.embedding_status = "pending"
                kb_chunk.access_snapshot = self._access_snapshot(document, metadata)
                kb_chunk.quality_status = quality
                updated += 1
            kb_chunks.append(kb_chunk)

        await self.db.flush()

        if job is not None:
            await self._set_job_stage(job, "embeddings")

        from app.documents.chunk_utils import chunk_embedding_text

        texts = [chunk_embedding_text(chunk) for chunk in chunks]
        embeddings = await self.embedding_service.embed_texts(texts)
        if job is not None:
            await self._set_job_stage(job, "qdrant")
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
        fts_count = 0
        if job is not None:
            await self._set_job_stage(job, "fulltext")
        for chunk, kb_chunk, embedding in zip(chunks, kb_chunks, embeddings.items, strict=True):
            kb_chunk.embedding_status = "indexed"
            kb_chunk.indexed_at = now
            chunk.embedding_model = embedding.model
            chunk.qdrant_collection = kb.qdrant_collection
            chunk.qdrant_point_id = str(kb_chunk.id)
            chunk.vector_id = str(kb_chunk.id)
            chunk.is_indexed = True
            if not kb_chunk.is_excluded_from_search:
                await self.fts.index_chunk(kb_chunk, chunk)
                fts_count += 1

        if job is not None:
            job.embedded_chunks_count += len([c for c in kb_chunks if c.embedding_status == "indexed"])
            job.qdrant_points_count += len(points)
            job.fulltext_chunks_count += fts_count
            await self._update_job_progress(job)

        source.processing_status = KnowledgeBaseSourceStatus.READY
        source.last_indexed_at = now
        source.fragments_count = len(kb_chunks)
        source.pages_count = version.pages_count
        source.quality_status = self._source_quality(kb_chunks)
        version.is_indexed = True
        version.qdrant_collection = kb.qdrant_collection
        version.qdrant_points_count = len(points)
        document.is_indexed = True
        await self.db.flush()
        return {
            "created_fragments_count": created,
            "updated_fragments_count": updated,
            "chunks_count": len(kb_chunks),
            "embedded_chunks_count": len([c for c in kb_chunks if c.embedding_status == "indexed"]),
            "qdrant_points_count": len(points),
            "fulltext_chunks_count": fts_count,
        }

    async def _run_qc(self, kb: KnowledgeBase, job: KnowledgeBaseIndexingJob) -> None:
        empty_chunks = await self.db.scalar(
            select(func.count(KnowledgeBaseChunk.id))
            .join(DocumentChunk, DocumentChunk.id == KnowledgeBaseChunk.document_chunk_id)
            .where(
                KnowledgeBaseChunk.knowledge_base_id == kb.id,
                DocumentChunk.text.is_(None),
                DocumentChunk.content.is_(None),
            )
        )
        low_quality = await self.db.scalar(
            select(func.count(KnowledgeBaseChunk.id)).where(
                KnowledgeBaseChunk.knowledge_base_id == kb.id,
                KnowledgeBaseChunk.quality_status == KnowledgeBaseChunkQualityStatus.LOW,
            )
        )
        fts_count = await self.db.scalar(
            select(func.count(KnowledgeBaseChunk.id)).where(
                KnowledgeBaseChunk.knowledge_base_id == kb.id,
                KnowledgeBaseChunk.search_vector.is_not(None),
            )
        )
        job.fulltext_chunks_count = int(fts_count or 0)
        metadata = kb.metadata_ or {}
        if empty_chunks or low_quality or job.errors_count > 0:
            metadata["qc_warnings"] = {
                "empty_chunks": int(empty_chunks or 0),
                "low_quality_chunks": int(low_quality or 0),
                "errors": job.errors_count,
            }
            kb.metadata_ = metadata
        elif "qc_warnings" in metadata:
            metadata.pop("qc_warnings")
            kb.metadata_ = metadata or None

    def _chunk_quality(self, chunk: DocumentChunk, metadata: dict[str, Any]) -> KnowledgeBaseChunkQualityStatus:
        from app.documents.chunk_utils import chunk_embedding_text

        text = chunk_embedding_text(chunk)
        if not text:
            return KnowledgeBaseChunkQualityStatus.FAILED
        quality_notes = metadata.get("quality_notes") or metadata.get("ocr_quality")
        if quality_notes and str(quality_notes).lower() in {"low", "poor", "bad"}:
            return KnowledgeBaseChunkQualityStatus.LOW
        if len(text) < 20:
            return KnowledgeBaseChunkQualityStatus.LOW
        if metadata.get("ocr_used"):
            return KnowledgeBaseChunkQualityStatus.MEDIUM
        return KnowledgeBaseChunkQualityStatus.GOOD

    def _source_quality(self, kb_chunks: list[KnowledgeBaseChunk]) -> KnowledgeBaseChunkQualityStatus:
        if not kb_chunks:
            return KnowledgeBaseChunkQualityStatus.FAILED
        statuses = [chunk.quality_status for chunk in kb_chunks]
        if any(status == KnowledgeBaseChunkQualityStatus.FAILED for status in statuses):
            return KnowledgeBaseChunkQualityStatus.FAILED
        if any(status == KnowledgeBaseChunkQualityStatus.LOW for status in statuses):
            return KnowledgeBaseChunkQualityStatus.LOW
        if any(status == KnowledgeBaseChunkQualityStatus.MEDIUM for status in statuses):
            return KnowledgeBaseChunkQualityStatus.MEDIUM
        return KnowledgeBaseChunkQualityStatus.GOOD

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

    async def _ensure_source_document_processed(self, document: Document, version: DocumentVersion) -> None:
        existing = await self._load_document_chunks(version.id)
        if existing and not self._chunks_outdated(existing):
            return

        content_type = (document.content_type or document.mime_type or "").lower()
        filename = (document.original_filename or version.original_filename or "").lower()
        try:
            if "pdf" in content_type or filename.endswith(".pdf"):
                from app.services.document_processing.parsers.pdf_parser import PdfParsingService

                await PdfParsingService(self.db).parse_document(document_version_id=version.id)
            elif "word" in content_type or "docx" in content_type or filename.endswith((".docx", ".doc")):
                from app.services.document_processing.parsers.docx_parser import DocxParsingService

                await DocxParsingService(self.db).parse_document(document_version_id=version.id)
            elif "sheet" in content_type or "excel" in content_type or filename.endswith((".xlsx", ".xls")):
                from app.services.document_processing.parsers.xlsx_parser import XlsxParsingService

                await XlsxParsingService(self.db).parse_document(document_version_id=version.id)
            elif content_type.startswith("image/"):
                from app.services.document_processing.parsers.imageparser import ImageParsingService

                await ImageParsingService(self.db).parse_document(document_version_id=version.id)
            else:
                raise KnowledgeBaseIndexingError(f"Неподдерживаемый формат: {content_type or filename}")
        except KnowledgeBaseIndexingError:
            if existing:
                # Перепарсить не получилось (например, формат не поддерживается),
                # но старые чанки есть — продолжаем работать с ними.
                return
            raise
        except Exception as exc:
            if existing:
                return
            raise KnowledgeBaseIndexingError(f"Не удалось обработать документ «{document.title}»: {exc}") from exc

    def _chunks_outdated(self, chunks: list[DocumentChunk]) -> bool:
        """Чанки устарели, если созданы старой версией алгоритма chunking."""
        from app.services.document_processing.chunking import DocumentChunkingService

        current = DocumentChunkingService.CHUNKING_VERSION
        for chunk in chunks:
            metadata = chunk.metadata_ or chunk.chunk_metadata or {}
            version = (metadata.get("chunking") or {}).get("version")
            if version != current:
                return True
        return False

    async def _resolve_final_kb_status_async(
        self,
        kb: KnowledgeBase,
        job: KnowledgeBaseIndexingJob,
    ) -> KnowledgeBaseStatus:
        # «Готова» выставляем только когда не осталось других активных job'ов:
        # иначе база мигает статусом, пока в очереди ждёт следующая индексация.
        if await self._has_other_active_jobs(kb.id, job.id):
            return KnowledgeBaseStatus.PROCESSING
        return self._resolve_final_kb_status(kb, job)

    def _resolve_final_kb_status(self, kb: KnowledgeBase, job: KnowledgeBaseIndexingJob) -> KnowledgeBaseStatus:
        if job.errors_count > 0 and job.processed_sources_count == 0:
            return KnowledgeBaseStatus.ERROR
        if job.errors_count > 0:
            return KnowledgeBaseStatus.NEEDS_REVIEW
        metadata = kb.metadata_ or {}
        qc_warnings = metadata.get("qc_warnings") or {}
        if qc_warnings.get("empty_chunks") or qc_warnings.get("low_quality_chunks"):
            return KnowledgeBaseStatus.NEEDS_REVIEW
        if (kb.fragments_count or 0) == 0 and job.processed_sources_count == 0:
            return KnowledgeBaseStatus.ERROR
        return KnowledgeBaseStatus.READY

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
            "source_status": "current" if version.is_current else "archived",
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
        await self._set_job_stage(job, "precheck")
        await self.db.flush()

    async def _set_job_stage(self, job: KnowledgeBaseIndexingJob, stage: str, *, emit: bool = True) -> None:
        params = dict(job.processing_params or {})
        params["current_stage"] = stage
        job.processing_params = params
        await self.db.flush()
        if emit:
            await self._emit_indexing_event(job, event="stage")

    async def _emit_indexing_event(
        self,
        job: KnowledgeBaseIndexingJob,
        *,
        event: str = "progress",
        kb: KnowledgeBase | None = None,
    ) -> None:
        knowledge_base = kb or await self.db.get(KnowledgeBase, job.knowledge_base_id)
        if knowledge_base is None:
            return
        payload = build_indexing_payload(
            event=event,
            knowledge_base=knowledge_base,
            job=job,
            indexing_active=is_indexing_active(knowledge_base, job),
        )
        publish_indexing_event(knowledge_base.id, payload)

    async def _check_cancel_requested(
        self,
        job: KnowledgeBaseIndexingJob,
        kb: KnowledgeBase,
        started: float,
    ) -> bool:
        await self.db.refresh(job)
        if not job.cancel_requested:
            return False
        await self._mark_job_cancelled(job, kb, started)
        return True

    async def _mark_job_cancelled(
        self,
        job: KnowledgeBaseIndexingJob,
        kb: KnowledgeBase,
        started: float,
    ) -> None:
        job.status = KnowledgeBaseIndexJobStatus.CANCELLED
        job.finished_at = _now()
        job.duration_ms = int((perf_counter() - started) * 1000)
        await self._set_job_stage(job, "stopped", emit=False)
        metadata = kb.metadata_ or {}
        previous_status = metadata.pop("indexing_previous_status", None)
        kb.metadata_ = metadata or None
        if previous_status and kb.fragments_count > 0:
            try:
                kb.status = KnowledgeBaseStatus(previous_status)
            except ValueError:
                kb.status = KnowledgeBaseStatus.READY
        elif kb.fragments_count > 0:
            kb.status = KnowledgeBaseStatus.READY
        else:
            kb.status = KnowledgeBaseStatus.DRAFT
        for source in kb.sources:
            if source.processing_status == KnowledgeBaseSourceStatus.PROCESSING:
                source.processing_status = KnowledgeBaseSourceStatus.READY_TO_INDEX
        await self.db.flush()
        await self._emit_indexing_event(job, event="cancelled", kb=kb)

    async def _update_job_progress(self, job: KnowledgeBaseIndexingJob) -> None:
        await self.db.flush()
        await self._emit_indexing_event(job, event="progress")

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
        await self._emit_indexing_event(job, event="completed")

    async def _mark_job_failed(self, job: KnowledgeBaseIndexingJob, started: float) -> None:
        job.status = KnowledgeBaseIndexJobStatus.FAILED
        job.finished_at = _now()
        job.duration_ms = int((perf_counter() - started) * 1000)
        await self.db.flush()
        await self._emit_indexing_event(job, event="failed")

    async def abort_job_from_worker(self, job_id: uuid.UUID, *, error_message: str) -> None:
        job = await self.db.get(KnowledgeBaseIndexingJob, job_id)
        if job is None:
            return
        if job.status not in {KnowledgeBaseIndexJobStatus.QUEUED, KnowledgeBaseIndexJobStatus.RUNNING}:
            return
        kb = await self._load_kb(job.knowledge_base_id)
        elapsed = (_now() - job.started_at).total_seconds() if job.started_at else 0.0
        started = perf_counter() - max(0.0, elapsed)
        await self._record_error(
            job,
            KnowledgeBaseIndexErrorType.QDRANT_WRITE_FAILED,
            error_message,
            user_message="Индексация прервана на стороне worker",
            recommended_action="Повторите запуск индексации",
        )
        await self._mark_job_failed(job, started)
        metadata = kb.metadata_ or {}
        previous_status = metadata.pop("indexing_previous_status", None)
        kb.metadata_ = metadata or None
        if previous_status and kb.fragments_count > 0:
            try:
                kb.status = KnowledgeBaseStatus(previous_status)
            except ValueError:
                kb.status = KnowledgeBaseStatus.READY
        elif kb.fragments_count > 0:
            kb.status = KnowledgeBaseStatus.READY
        else:
            kb.status = KnowledgeBaseStatus.DRAFT
        for source in kb.sources:
            if source.processing_status == KnowledgeBaseSourceStatus.PROCESSING:
                source.processing_status = KnowledgeBaseSourceStatus.READY_TO_INDEX
        await self.db.flush()

    async def _record_error(
        self,
        job: KnowledgeBaseIndexingJob,
        error_type: KnowledgeBaseIndexErrorType,
        technical_message: str,
        *,
        source_id: uuid.UUID | None = None,
        user_message: str | None = None,
        recommended_action: str | None = None,
    ) -> None:
        job.errors_count += 1
        self.db.add(
            KnowledgeBaseIndexingErrorModel(
                job_id=job.id,
                knowledge_base_id=job.knowledge_base_id,
                source_id=source_id,
                error_type=error_type,
                technical_message=technical_message,
                user_message=user_message or "Индексация базы знаний завершилась с ошибкой.",
                recommended_action=recommended_action or "Проверьте источник и запустите повторную обработку.",
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
            "total_sources_count": job.total_sources_count,
            "total_chunks_count": job.total_chunks_count,
            "extracted_sources_count": job.extracted_sources_count,
            "chunked_sources_count": job.chunked_sources_count,
            "embedded_chunks_count": job.embedded_chunks_count,
            "qdrant_points_count": job.qdrant_points_count,
            "fulltext_chunks_count": job.fulltext_chunks_count,
            "embedding_model": job.embedding_model,
            "qdrant_collection": job.qdrant_collection,
            "duration_ms": job.duration_ms,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)
