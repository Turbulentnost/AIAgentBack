from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.workers.celery_app import celery_app


def _run_async_task(factory):
    import asyncio

    from app.db.session import engine
    from app.integrations.qdrant import qdrant_client

    async def runner():
        qdrant_client.reset_client()
        try:
            return await factory()
        finally:
            await qdrant_client.aclose()
            await engine.dispose()

    return asyncio.run(runner())


@celery_app.task(name="debug_task", bind=True)
def debug_task(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "message": "Celery worker executed debug_task",
        "payload": payload or {},
        "request_id": self.request.id,
        "queue": self.request.delivery_info.get("routing_key"),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


@celery_app.task(name="archive_expired_scheduled_meetings")
def archive_expired_scheduled_meetings() -> dict[str, Any]:
    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.services.scheduled_meeting_service import ScheduledMeetingService

    if not settings.SCHEDULED_MEETINGS_ARCHIVE_ENABLED:
        return {
            "skipped": True,
            "reason": "archive_disabled",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _archive() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            result = await ScheduledMeetingService(db).archive_expired_series()
            await db.commit()
            return {
                **result,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }

    return _run_async_task(_archive)


@celery_app.task(name="sync_scheduled_meeting_registry_cards")
def sync_scheduled_meeting_registry_cards() -> dict[str, Any]:
    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.services.scheduled_meeting_registry_sync import ScheduledMeetingRegistrySyncService

    if not settings.SCHEDULED_MEETINGS_CARD_SYNC_ENABLED:
        return {
            "skipped": True,
            "reason": "card_sync_disabled",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _sync() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            result = await ScheduledMeetingRegistrySyncService(db).sync_all_due_series()
            await db.commit()
            return {
                "processed": result.processed,
                "created": result.created,
                "rolled": result.rolled,
                "updated": result.updated,
                "skipped": result.skipped,
                "no_occurrences": result.no_occurrences,
                "errors": result.errors,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }

    return _run_async_task(_sync)


@celery_app.task(name="warm_meeting_dashboard_cache")
def warm_meeting_dashboard_cache() -> dict[str, Any]:
    from app.core.config import settings
    from app.services.meeting_dashboard_cache import MeetingDashboardCacheService

    if not settings.MEETING_DASHBOARD_CACHE_ENABLED or not settings.MEETING_DASHBOARD_CACHE_WARMUP_ENABLED:
        return {
            "skipped": True,
            "reason": "cache_or_warmup_disabled",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _warm() -> dict[str, Any]:
        result = await MeetingDashboardCacheService().warmup()
        return {
            **result,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    return _run_async_task(_warm)


@celery_app.task(name="create_registry_protocol_draft", bind=True, max_retries=2)
def create_registry_protocol_draft(self, entry_id: str) -> dict[str, Any]:
    import uuid as uuid_module

    from app.db.session import AsyncSessionLocal
    from app.services.meeting_protocol_draft_service import MeetingProtocolDraftService

    async def _create() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            service = MeetingProtocolDraftService(db)
            try:
                result = await service.create_protocol_draft_for_entry(uuid_module.UUID(entry_id))
                await db.commit()
                return {
                    **result,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as exc:
                await db.commit()
                raise exc

    try:
        return _run_async_task(_create)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60) from exc


@celery_app.task(name="dispatch_meeting_protocol_drafts")
def dispatch_meeting_protocol_drafts() -> dict[str, Any]:
    from app.core.config import settings
    from app.services.meeting_protocol_dispatch_service import run_protocol_draft_dispatch

    if not settings.MEETING_PROTOCOL_DRAFT_ENABLED:
        return {
            "skipped": True,
            "reason": "protocol_draft_disabled",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
    if not settings.MEETING_PROTOCOL_DISPATCH_BEAT_ENABLED:
        return {
            "skipped": True,
            "reason": "dispatch_beat_disabled",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _dispatch() -> dict[str, Any]:
        result = await run_protocol_draft_dispatch()
        return {
            **result,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    return _run_async_task(_dispatch)


@celery_app.task(name="run_task", bind=True, max_retries=3)
def run_task(self, task_id: str, task_type: str | None, input_payload: dict) -> dict[str, Any]:
    if task_type == "meeting":
        return run_meeting_task(task_id)
    import asyncio

    from app.orchestrator.orchestrator import orchestrator

    return asyncio.run(orchestrator.run(task_type, input_payload))


@celery_app.task(name="run_meeting_task", bind=True, max_retries=3)
def run_meeting_task(task_id: str) -> dict[str, Any]:
    import uuid as uuid_module

    from app.db.session import AsyncSessionLocal
    from app.services.meeting_service import MeetingService

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            service = MeetingService(db)
            try:
                result = await service.execute_task(uuid_module.UUID(task_id))
                await db.commit()
                return {
                    "task_id": task_id,
                    "task_name": "run_meeting_task",
                    "status": "completed",
                    "result": result,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as exc:  # noqa: BLE001
                await db.commit()
                return {
                    "task_id": task_id,
                    "task_name": "run_meeting_task",
                    "status": "failed",
                    "error": str(exc),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }

    return _run_async_task(_run)


@celery_app.task(name="run_sandbox", bind=True)
def run_sandbox(self, run_id: str) -> dict[str, Any]:
    import asyncio
    import uuid

    from fastapi.encoders import jsonable_encoder

    from app.agents.runtime.consultant_runner import consultant_runner
    from app.db.session import AsyncSessionLocal
    from app.models.agent_builder_sandbox import AgentBuilderSandboxRun, AgentBuilderSandboxStep
    from app.models.agent_builder_session import AgentBuilderSession
    from app.models.user import User

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            run = await db.get(AgentBuilderSandboxRun, uuid.UUID(run_id))
            if run is None:
                return {"status": "failed", "error": "Sandbox run не найден", "run_id": run_id}

            session = await db.get(AgentBuilderSession, run.session_id)
            user = (
                await db.get(User, run.requested_by_user_id)
                if run.requested_by_user_id is not None
                else None
            )
            blueprint = (session.proposed_agent_structure if session else None) or {}
            test_query = run.test_query or (session.goal if session else "") or ""

            run.status = "running"
            run.started_at = datetime.now(timezone.utc)
            await db.commit()

            async def on_step_start(info: dict[str, Any]) -> AgentBuilderSandboxStep:
                step = AgentBuilderSandboxStep(
                    run_id=run.id,
                    order_index=info["order_index"],
                    title=info.get("title"),
                    capability=info.get("capability"),
                    tool_name=info.get("tool_name"),
                    status="running",
                    request=jsonable_encoder(info.get("request")),
                    started_at=datetime.now(timezone.utc),
                )
                db.add(step)
                await db.commit()
                return step

            async def on_step_finish(step: AgentBuilderSandboxStep, record: dict[str, Any]) -> None:
                step.status = record.get("status") or "completed"
                step.result_summary = jsonable_encoder(record.get("result_summary"))
                step.duration_ms = record.get("duration_ms")
                step.error_message = record.get("error_message")
                step.finished_at = datetime.now(timezone.utc)
                await db.commit()

            try:
                result = await consultant_runner.execute(
                    blueprint=blueprint,
                    test_query=test_query,
                    db=db,
                    user=user,
                    on_step_start=on_step_start,
                    on_step_finish=on_step_finish,
                )
                run.final_answer = result.final_answer
                run.stats = jsonable_encoder(result.stats)
                run.executed_graph = jsonable_encoder(result.executed_graph)
                run.status = "succeeded"
                run.finished_at = datetime.now(timezone.utc)
                await db.commit()
                return {
                    "status": "succeeded",
                    "run_id": run_id,
                    "celery_task_id": self.request.id,
                    "finished_at": run.finished_at.isoformat(),
                }
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                run = await db.get(AgentBuilderSandboxRun, uuid.UUID(run_id))
                if run is not None:
                    run.status = "failed"
                    run.error_message = str(exc)
                    run.finished_at = datetime.now(timezone.utc)
                    await db.commit()
                return {"status": "failed", "run_id": run_id, "error": str(exc)}

    return asyncio.run(_run())


@celery_app.task(name="process_document", bind=True, max_retries=3)
def process_document(self, document_id: str) -> dict[str, Any]:
    import asyncio
    import uuid

    from app.db.session import AsyncSessionLocal
    from app.models.document import Document
    from app.services.document_processing.parsers.docx_parser import DocxParsingError, DocxParsingService
    from app.services.document_processing.parsers.imageparser import ImageParsingError, ImageParsingService
    from app.services.document_processing.parsers.pdf_parser import PdfParsingError, PdfParsingService
    from app.services.document_processing.parsers.xlsx_parser import XlsxParsingError, XlsxParsingService

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            try:
                document_uuid = uuid.UUID(document_id)
                document = await db.get(Document, document_uuid)
                if document is None:
                    return {
                        "celery_task_id": self.request.id,
                        "task_name": "process_document",
                        "document_id": document_id,
                        "status": "failed",
                        "error": "Документ не найден",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }

                content_type = document.content_type or ""
                if "pdf" in content_type:
                    result = await PdfParsingService(db).parse_document(document_id=document_uuid)
                elif "word" in content_type or "docx" in content_type:
                    result = await DocxParsingService(db).parse_document(document_id=document_uuid)
                elif "sheet" in content_type or "excel" in content_type or "xlsx" in content_type:
                    result = await XlsxParsingService(db).parse_document(document_id=document_uuid)
                elif content_type.startswith("image/"):
                    result = await ImageParsingService(db).parse_document(document_id=document_uuid)
                else:
                    return {
                        "celery_task_id": self.request.id,
                        "task_name": "process_document",
                        "document_id": document_id,
                        "status": "failed",
                        "error": f"Неподдерживаемый тип документа для обработки: {content_type}",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }

                await db.commit()
                return {
                    "celery_task_id": self.request.id,
                    "task_name": "process_document",
                    "document_id": str(result.document_id),
                    "document_version_id": str(result.document_version_id),
                    "status": "partial" if getattr(result, "failed_pages", []) else "completed",
                    "pages_count": getattr(result, "pages_count", 1),
                    "characters_count": result.characters_count,
                    "extraction_method": result.extraction_method,
                    "requires_ocr": getattr(result, "requires_ocr", True),
                    "ocr_used": getattr(result, "ocr_used", True),
                    "failed_pages": getattr(result, "failed_pages", []),
                    "extracted_text_object_name": result.extracted_text_object_name,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            except (PdfParsingError, ImageParsingError, DocxParsingError, XlsxParsingError) as exc:
                await db.commit()
                return {
                    "celery_task_id": self.request.id,
                    "task_name": "process_document",
                    "document_id": document_id,
                    "status": "failed",
                    "error": str(exc),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }

    return asyncio.run(_run())


@celery_app.task(name="run_agent", bind=True, max_retries=3)
def run_agent(self, agent_id: str, task_id: str, input_payload: dict | None = None) -> dict[str, Any]:
    return _placeholder_result(
        self.request.id,
        "run_agent",
        agent_id=agent_id,
        task_id=task_id,
        input_payload=input_payload or {},
    )


@celery_app.task(name="index_document", bind=True, max_retries=3)
def index_document(self, document_id: str) -> dict[str, Any]:
    import asyncio
    import uuid

    from app.db.session import AsyncSessionLocal
    from app.services.document_processing.indexing import QdrantIndexingError, QdrantIndexingService
    from app.services.embeddings import (
        EmbeddingBatchError,
        EmbeddingConfigurationError,
        EmbeddingProviderUnavailableError,
        EmbeddingVectorSizeMismatchError,
    )

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            try:
                result = await QdrantIndexingService(db).index_document(uuid.UUID(document_id))
                await db.commit()
                return {
                    "celery_task_id": self.request.id,
                    "task_name": "index_document",
                    "status": "completed",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    **result,
                }
            except (
                QdrantIndexingError,
                EmbeddingBatchError,
                EmbeddingConfigurationError,
                EmbeddingProviderUnavailableError,
                EmbeddingVectorSizeMismatchError,
                ValueError,
            ) as exc:
                await db.rollback()
                return {
                    "celery_task_id": self.request.id,
                    "task_name": "index_document",
                    "document_id": document_id,
                    "status": "failed",
                    "error": str(exc),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }

    return asyncio.run(_run())


@celery_app.task(
    name="index_knowledge_base_full",
    bind=True,
    max_retries=0,
    acks_late=True,
    reject_on_worker_lost=False,
)
def index_knowledge_base_full(self, knowledge_base_id: str, job_id: str | None = None) -> dict[str, Any]:
    import uuid

    from app.db.session import AsyncSessionLocal
    from app.models.enums import KnowledgeBaseIndexJobStatus
    from app.models.knowledge_base import KnowledgeBaseIndexingJob
    from app.services.knowledge_base_celery import is_celery_task_cancelled
    from app.services.knowledge_base_indexing_service import KnowledgeBaseIndexingService

    if is_celery_task_cancelled(self.request.id):
        return {
            "celery_task_id": self.request.id,
            "task_name": "index_knowledge_base_full",
            "knowledge_base_id": knowledge_base_id,
            "job_id": job_id,
            "status": "cancelled",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            if job_id:
                job = await db.get(KnowledgeBaseIndexingJob, uuid.UUID(job_id))
                if job is not None and (
                    job.status == KnowledgeBaseIndexJobStatus.CANCELLED or job.cancel_requested
                ):
                    return {
                        "celery_task_id": self.request.id,
                        "task_name": "index_knowledge_base_full",
                        "knowledge_base_id": knowledge_base_id,
                        "job_id": job_id,
                        "status": "cancelled",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }

            service = KnowledgeBaseIndexingService(db)
            try:
                if job_id:
                    result = await service.run_job(uuid.UUID(job_id))
                else:
                    result = await service.index_knowledge_base(uuid.UUID(knowledge_base_id))
                await db.commit()
                result_status = result.get("status")
                if result_status in {"cancelled", "CANCELLED"}:
                    status = "cancelled"
                else:
                    status = "completed"
                return {
                    "celery_task_id": self.request.id,
                    "task_name": "index_knowledge_base_full",
                    "status": status,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    **result,
                }
            except Exception as exc:
                await db.rollback()
                if job_id:
                    async with AsyncSessionLocal() as cleanup_db:
                        job = await cleanup_db.get(KnowledgeBaseIndexingJob, uuid.UUID(job_id))
                        if job is not None and (
                            job.status == KnowledgeBaseIndexJobStatus.CANCELLED or job.cancel_requested
                        ):
                            return {
                                "celery_task_id": self.request.id,
                                "task_name": "index_knowledge_base_full",
                                "knowledge_base_id": knowledge_base_id,
                                "job_id": job_id,
                                "status": "cancelled",
                                "finished_at": datetime.now(timezone.utc).isoformat(),
                            }
                        await KnowledgeBaseIndexingService(cleanup_db).abort_job_from_worker(
                            uuid.UUID(job_id),
                            error_message=str(exc),
                        )
                        await cleanup_db.commit()
                return {
                    "celery_task_id": self.request.id,
                    "task_name": "index_knowledge_base_full",
                    "knowledge_base_id": knowledge_base_id,
                    "job_id": job_id,
                    "status": "failed",
                    "error": str(exc),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }

    return _run_async_task(_run)


@celery_app.task(
    name="index_knowledge_base_source",
    bind=True,
    max_retries=0,
    acks_late=True,
    reject_on_worker_lost=False,
)
def index_knowledge_base_source(
    self,
    knowledge_base_id: str,
    source_id: str,
    job_id: str | None = None,
) -> dict[str, Any]:
    import uuid

    from app.db.session import AsyncSessionLocal
    from app.models.enums import KnowledgeBaseIndexJobStatus
    from app.models.knowledge_base import KnowledgeBaseIndexingJob
    from app.services.knowledge_base_celery import is_celery_task_cancelled
    from app.services.knowledge_base_indexing_service import KnowledgeBaseIndexingService

    if is_celery_task_cancelled(self.request.id):
        return {
            "celery_task_id": self.request.id,
            "task_name": "index_knowledge_base_source",
            "knowledge_base_id": knowledge_base_id,
            "source_id": source_id,
            "job_id": job_id,
            "status": "cancelled",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            if job_id:
                job = await db.get(KnowledgeBaseIndexingJob, uuid.UUID(job_id))
                if job is not None and (
                    job.status == KnowledgeBaseIndexJobStatus.CANCELLED or job.cancel_requested
                ):
                    return {
                        "celery_task_id": self.request.id,
                        "task_name": "index_knowledge_base_source",
                        "knowledge_base_id": knowledge_base_id,
                        "source_id": source_id,
                        "job_id": job_id,
                        "status": "cancelled",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }

            service = KnowledgeBaseIndexingService(db)
            try:
                result = (
                    await service.run_job(uuid.UUID(job_id))
                    if job_id
                    else await service.index_source(uuid.UUID(knowledge_base_id), uuid.UUID(source_id))
                )
                await db.commit()
                result_status = result.get("status")
                status = "cancelled" if result_status in {"cancelled", "CANCELLED"} else "completed"
                return {
                    "celery_task_id": self.request.id,
                    "task_name": "index_knowledge_base_source",
                    "status": status,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    **result,
                }
            except Exception as exc:
                await db.rollback()
                if job_id:
                    async with AsyncSessionLocal() as cleanup_db:
                        job = await cleanup_db.get(KnowledgeBaseIndexingJob, uuid.UUID(job_id))
                        if job is not None and (
                            job.status == KnowledgeBaseIndexJobStatus.CANCELLED or job.cancel_requested
                        ):
                            return {
                                "celery_task_id": self.request.id,
                                "task_name": "index_knowledge_base_source",
                                "knowledge_base_id": knowledge_base_id,
                                "source_id": source_id,
                                "job_id": job_id,
                                "status": "cancelled",
                                "finished_at": datetime.now(timezone.utc).isoformat(),
                            }
                        await KnowledgeBaseIndexingService(cleanup_db).abort_job_from_worker(
                            uuid.UUID(job_id),
                            error_message=str(exc),
                        )
                        await cleanup_db.commit()
                return {
                    "celery_task_id": self.request.id,
                    "task_name": "index_knowledge_base_source",
                    "knowledge_base_id": knowledge_base_id,
                    "source_id": source_id,
                    "job_id": job_id,
                    "status": "failed",
                    "error": str(exc),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }

    return _run_async_task(_run)


@celery_app.task(name="recover_stale_knowledge_base_indexing_jobs", bind=True, max_retries=0)
def recover_stale_knowledge_base_indexing_jobs(self) -> dict[str, Any]:
    """Cancel stale/duplicated KB indexing jobs and requeue recoverable ones."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.models.enums import (
        KnowledgeBaseIndexJobStatus,
        KnowledgeBaseIndexJobType,
        KnowledgeBaseSourceStatus,
        KnowledgeBaseStatus,
    )
    from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseIndexingJob
    from app.services.knowledge_base_celery import (
        attach_celery_task_id,
        read_celery_task_id,
        revoke_active_indexing_tasks,
        revoke_indexing_celery_task,
    )
    from app.services.knowledge_base_indexing_service import KnowledgeBaseIndexingService

    indexing_task_names = {
        "index_knowledge_base_full",
        "index_knowledge_base_source",
        "reindex_knowledge_base_embeddings",
        "reindex_knowledge_base_after_access_change",
    }

    def active_tasks_by_job() -> dict[str, list[str]]:
        try:
            active = celery_app.control.inspect(timeout=2.0).active() or {}
        except Exception:
            return {}
        result: dict[str, list[str]] = {}
        for tasks in active.values():
            for task in tasks:
                if str(task.get("name") or "") not in indexing_task_names:
                    continue
                args = [str(item) for item in (task.get("args") or [])]
                if len(args) < 2:
                    continue
                task_id = str(task.get("id") or "")
                if task_id:
                    result.setdefault(args[-1], []).append(task_id)
        return result

    async def _run() -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=settings.KB_INDEXING_STALE_AFTER_SECONDS)
        active_by_job = active_tasks_by_job()
        recovered: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        async with AsyncSessionLocal() as db:
            query = (
                select(KnowledgeBaseIndexingJob)
                .options(
                    selectinload(KnowledgeBaseIndexingJob.knowledge_base).selectinload(
                        KnowledgeBase.sources
                    )
                )
                .where(
                    KnowledgeBaseIndexingJob.status.in_(
                        [KnowledgeBaseIndexJobStatus.QUEUED, KnowledgeBaseIndexJobStatus.RUNNING]
                    )
                )
                .order_by(KnowledgeBaseIndexingJob.updated_at.asc())
                .limit(settings.KB_INDEXING_RECOVERY_MAX_JOBS)
            )
            jobs = list((await db.execute(query)).scalars().all())
            service = KnowledgeBaseIndexingService(db)

            for job in jobs:
                kb = job.knowledge_base
                if kb is None:
                    continue
                updated_at = job.updated_at
                if updated_at is not None and updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                is_stale = updated_at is None or updated_at <= stale_before
                active_task_ids = active_by_job.get(str(job.id), [])
                has_duplicates = len(active_task_ids) > 1
                is_orphan = job.status == KnowledgeBaseIndexJobStatus.RUNNING and not active_task_ids
                is_stale_queued = job.status == KnowledgeBaseIndexJobStatus.QUEUED and is_stale

                if not (has_duplicates or is_orphan or is_stale_queued):
                    skipped.append(
                        {
                            "job_id": str(job.id),
                            "knowledge_base_id": str(job.knowledge_base_id),
                            "active_tasks": len(active_task_ids),
                            "reason": "active_or_recent",
                        }
                    )
                    continue

                revoke_indexing_celery_task(read_celery_task_id(job.processing_params), terminate=True)
                revoke_active_indexing_tasks(
                    knowledge_base_id=str(job.knowledge_base_id),
                    job_ids=[str(job.id)],
                    terminate=True,
                )

                job.status = KnowledgeBaseIndexJobStatus.CANCELLED
                job.cancel_requested = True
                job.cancel_requested_at = now
                job.cancel_reason = "Автовосстановление зависшей индексации"
                job.finished_at = now
                job.processing_params = {
                    **(job.processing_params or {}),
                    "current_stage": "stopped",
                    "last_observed_stage": "stopped",
                    "recovered_by": "recover_stale_knowledge_base_indexing_jobs",
                }

                for source in kb.sources:
                    if source.processing_status == KnowledgeBaseSourceStatus.PROCESSING:
                        source.processing_status = KnowledgeBaseSourceStatus.READY_TO_INDEX

                requeued_job_id: str | None = None
                if kb.status != KnowledgeBaseStatus.ARCHIVED and kb.deleted_at is None:
                    await service.mark_indexing_queued(kb.id)
                    new_job = await service.create_job(
                        kb.id,
                        job_type=job.job_type,
                        started_by_user_id=job.started_by_user_id,
                        target_source_id=job.target_source_id,
                        target_chunk_id=job.target_chunk_id,
                    )
                    await db.flush()
                    if job.job_type == KnowledgeBaseIndexJobType.SOURCE and job.target_source_id is not None:
                        async_result = index_knowledge_base_source.apply_async(
                            args=[str(kb.id), str(job.target_source_id), str(new_job.id)],
                            queue="indexing",
                        )
                    elif job.job_type == KnowledgeBaseIndexJobType.EMBEDDINGS:
                        async_result = reindex_knowledge_base_embeddings.apply_async(
                            args=[str(kb.id), str(new_job.id)],
                            queue="indexing",
                        )
                    else:
                        async_result = index_knowledge_base_full.apply_async(
                            args=[str(kb.id), str(new_job.id)],
                            queue="indexing",
                        )
                    new_job.processing_params = attach_celery_task_id(
                        new_job.processing_params,
                        async_result.id,
                    )
                    requeued_job_id = str(new_job.id)
                elif kb.fragments_count > 0:
                    kb.status = KnowledgeBaseStatus.READY
                else:
                    kb.status = KnowledgeBaseStatus.DRAFT

                recovered.append(
                    {
                        "job_id": str(job.id),
                        "knowledge_base_id": str(job.knowledge_base_id),
                        "knowledge_base_name": kb.name,
                        "active_tasks": len(active_task_ids),
                        "reason": "duplicate_active_tasks" if has_duplicates else "stale_or_orphan",
                        "requeued_job_id": requeued_job_id,
                    }
                )

            await db.commit()

        return {
            "task_id": self.request.id,
            "recovered": recovered,
            "skipped": skipped,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    return _run_async_task(_run)


@celery_app.task(name="run_knowledge_base_search_query", bind=True, max_retries=0)
def run_knowledge_base_search_query(self, search_query_id: str) -> dict[str, Any]:
    """Фоновый поиск по базе знаний: выполняется до конца, даже если
    пользователь ушёл со страницы. Результат сохраняется в историю."""
    import uuid

    from fastapi.encoders import jsonable_encoder

    from app.db.session import AsyncSessionLocal
    from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseSearchQuery
    from app.models.user import User
    from app.services.knowledge_base_search_service import KnowledgeBaseSearchService

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            item = await db.get(KnowledgeBaseSearchQuery, uuid.UUID(search_query_id))
            if item is None:
                return {"status": "failed", "error": "Запрос не найден"}
            if item.cancel_requested or item.status == "cancelled":
                item.status = "cancelled"
                item.finished_at = datetime.now(timezone.utc)
                await db.commit()
                return {"status": "cancelled"}

            kb = await db.get(KnowledgeBase, item.knowledge_base_id)
            user = await db.get(User, item.user_id)
            if kb is None or user is None:
                item.status = "failed"
                item.error = "База знаний или пользователь не найдены"
                item.finished_at = datetime.now(timezone.utc)
                await db.commit()
                return {"status": "failed", "error": item.error}

            item.status = "running"
            await db.commit()

            try:
                result = await KnowledgeBaseSearchService(db).search(
                    knowledge_base=kb,
                    query=item.query,
                    user=user,
                    top_k=item.top_k,
                    include_inaccessible=True,
                    test_mode=True,
                    viewer=user,
                )
            except Exception as exc:
                await db.rollback()
                item = await db.get(KnowledgeBaseSearchQuery, uuid.UUID(search_query_id))
                if item is not None:
                    item.status = "failed"
                    item.error = str(exc)[:2000]
                    item.finished_at = datetime.now(timezone.utc)
                    await db.commit()
                return {"status": "failed", "error": str(exc)}

            # Пользователь мог запросить отмену, пока шёл поиск.
            await db.refresh(item)
            if item.cancel_requested:
                item.status = "cancelled"
                item.finished_at = datetime.now(timezone.utc)
                await db.commit()
                return {"status": "cancelled"}

            item.answer = result.answer_preview
            item.hits = jsonable_encoder(result.hits)
            item.status = "completed"
            item.finished_at = datetime.now(timezone.utc)
            await db.commit()
            return {"status": "completed", "hits_count": len(result.hits)}

    return _run_async_task(_run)


@celery_app.task(
    name="reindex_knowledge_base_embeddings",
    bind=True,
    max_retries=0,
    acks_late=True,
    reject_on_worker_lost=False,
)
def reindex_knowledge_base_embeddings(self, knowledge_base_id: str, job_id: str | None = None) -> dict[str, Any]:
    return index_knowledge_base_full.run(knowledge_base_id, job_id)


@celery_app.task(
    name="reindex_knowledge_base_after_access_change",
    bind=True,
    max_retries=0,
    acks_late=True,
    reject_on_worker_lost=False,
)
def reindex_knowledge_base_after_access_change(self, knowledge_base_id: str, job_id: str | None = None) -> dict[str, Any]:
    return index_knowledge_base_full.run(knowledge_base_id, job_id)


@celery_app.task(name="migrate_legacy_knowledge_base_documents", bind=True, max_retries=1)
def migrate_legacy_knowledge_base_documents(self) -> dict[str, Any]:
    import asyncio
    import uuid

    from sqlalchemy import select

    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.integrations.qdrant import qdrant_client
    from app.models.document import Document, DocumentVersion
    from app.models.enums import KnowledgeBaseAccessType, KnowledgeBaseGrantType, KnowledgeBaseStatus
    from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseAccessGrant
    from app.schemas.knowledge_base import KnowledgeBaseSourceCreate
    from app.services.knowledge_base_service import KnowledgeBaseService

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            legacy = await db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == "Legacy Knowledge Base"))
            if legacy is None:
                kb_id = uuid.uuid4()
                legacy = KnowledgeBase(
                    id=kb_id,
                    name="Legacy Knowledge Base",
                    description="Автоматически созданная база для документов, ранее отмеченных как is_knowledge_base.",
                    status=KnowledgeBaseStatus.NEEDS_REVIEW,
                    embedding_model=settings.EMBEDDINGS_MODEL,
                    vector_store="qdrant",
                    qdrant_collection=f"kb_{kb_id.hex}",
                    is_public=False,
                )
                db.add(legacy)
                await db.flush()
                db.add(
                    KnowledgeBaseAccessGrant(
                        knowledge_base_id=legacy.id,
                        grantee_type=KnowledgeBaseGrantType.ADMIN_ONLY,
                        grantee_id=None,
                        access_type=KnowledgeBaseAccessType.ADMIN,
                    )
                )
                await qdrant_client.ensure_collection(
                    collection=legacy.qdrant_collection,
                    vector_size=settings.EMBEDDINGS_VECTOR_SIZE,
                )

            result = await db.execute(select(Document).where(Document.is_knowledge_base.is_(True)))
            documents = list(result.scalars().all())
            service = KnowledgeBaseService(db)
            migrated = 0
            for document in documents:
                version = await db.scalar(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == document.id)
                    .order_by(DocumentVersion.is_current.desc(), DocumentVersion.version_number.desc())
                    .limit(1)
                )
                if version is None:
                    continue
                await service.add_source(
                    legacy.id,
                    KnowledgeBaseSourceCreate(document_id=document.id, document_version_id=version.id),
                    current_user=type("SystemUser", (), {"id": None, "department_id": None, "is_superuser": True})(),
                )
                document.is_knowledge_base = False
                migrated += 1
            await db.commit()
            return {
                "celery_task_id": self.request.id,
                "task_name": "migrate_legacy_knowledge_base_documents",
                "knowledge_base_id": str(legacy.id),
                "migrated_documents": migrated,
                "status": "completed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }

    return asyncio.run(_run())


@celery_app.task(name="run_department_analysis", bind=True, max_retries=0)
def run_department_analysis(self, run_id: str, force_reextract: bool = False) -> dict[str, Any]:
    import uuid as uuid_module

    from app.db.session import AsyncSessionLocal
    from app.services.department_analysis_service import DepartmentAnalysisService

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            service = DepartmentAnalysisService(db)
            run = await service.execute_department_analysis(
                uuid_module.UUID(run_id),
                force_reextract=force_reextract,
            )
            await db.commit()
            return {
                "celery_task_id": self.request.id,
                "task_name": "run_department_analysis",
                "run_id": run_id,
                "status": run.status.value,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            }

    return _run_async_task(_run)


@celery_app.task(name="classify_template_document", bind=True, max_retries=1)
def classify_template_document(self, document_link_id: str) -> dict[str, Any]:
    import uuid as uuid_module

    from app.db.session import AsyncSessionLocal
    from app.services.nd_template_classification_service import (
        NdTemplateClassificationService,
        NdTemplateClassificationServiceError,
    )

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            link_id = uuid_module.UUID(document_link_id)
            service = NdTemplateClassificationService(db)
            try:
                link = await service.classify_template_document(link_id)
                await db.commit()
                return {
                    "celery_task_id": self.request.id,
                    "task_name": "classify_template_document",
                    "document_link_id": document_link_id,
                    "status": link.classification_status.value,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            except NdTemplateClassificationServiceError as exc:
                await db.rollback()
                return {
                    "celery_task_id": self.request.id,
                    "task_name": "classify_template_document",
                    "document_link_id": document_link_id,
                    "status": "failed",
                    "error": str(exc),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }

    return _run_async_task(_run)


@celery_app.task(name="generate_report", bind=True, max_retries=3)
def generate_report(self, task_id: str, report_type: str = "default") -> dict[str, Any]:
    return _placeholder_result(self.request.id, "generate_report", task_id=task_id, report_type=report_type)


@celery_app.task(name="update_task_status", bind=True, max_retries=3)
def update_task_status(self, task_id: str, status: str) -> dict[str, Any]:
    return _placeholder_result(self.request.id, "update_task_status", task_id=task_id, status=status)


def _placeholder_result(celery_task_id: str, task_name: str, **payload: Any) -> dict[str, Any]:
    return {
        "celery_task_id": celery_task_id,
        "task_name": task_name,
        "status": "accepted",
        "payload": payload,
        "note": "Infrastructure placeholder task; business logic is not implemented yet.",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
