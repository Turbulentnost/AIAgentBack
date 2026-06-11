from __future__ import annotations

from datetime import datetime, timezone
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


@celery_app.task(name="run_task", bind=True, max_retries=3)
def run_task(self, task_id: str, task_type: str | None, input_payload: dict) -> dict[str, Any]:
    import asyncio

    from app.orchestrator.orchestrator import orchestrator

    return asyncio.run(orchestrator.run(task_type, input_payload))


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


@celery_app.task(name="index_knowledge_base_full", bind=True, max_retries=3)
def index_knowledge_base_full(self, knowledge_base_id: str, job_id: str | None = None) -> dict[str, Any]:
    import uuid

    from app.db.session import AsyncSessionLocal
    from app.services.knowledge_base_indexing_service import KnowledgeBaseIndexingService

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            service = KnowledgeBaseIndexingService(db)
            try:
                if job_id:
                    result = await service.run_job(uuid.UUID(job_id))
                else:
                    result = await service.index_knowledge_base(uuid.UUID(knowledge_base_id))
                await db.commit()
                return {
                    "celery_task_id": self.request.id,
                    "task_name": "index_knowledge_base_full",
                    "status": "completed",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    **result,
                }
            except Exception as exc:
                await db.rollback()
                if job_id:
                    async with AsyncSessionLocal() as cleanup_db:
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


@celery_app.task(name="index_knowledge_base_source", bind=True, max_retries=3)
def index_knowledge_base_source(
    self,
    knowledge_base_id: str,
    source_id: str,
    job_id: str | None = None,
) -> dict[str, Any]:
    import uuid

    from app.db.session import AsyncSessionLocal
    from app.services.knowledge_base_indexing_service import KnowledgeBaseIndexingService

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            service = KnowledgeBaseIndexingService(db)
            try:
                result = (
                    await service.run_job(uuid.UUID(job_id))
                    if job_id
                    else await service.index_source(uuid.UUID(knowledge_base_id), uuid.UUID(source_id))
                )
                await db.commit()
                return {
                    "celery_task_id": self.request.id,
                    "task_name": "index_knowledge_base_source",
                    "status": "completed",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    **result,
                }
            except Exception as exc:
                await db.rollback()
                if job_id:
                    async with AsyncSessionLocal() as cleanup_db:
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


@celery_app.task(name="reindex_knowledge_base_embeddings", bind=True, max_retries=3)
def reindex_knowledge_base_embeddings(self, knowledge_base_id: str, job_id: str | None = None) -> dict[str, Any]:
    return index_knowledge_base_full.run(knowledge_base_id, job_id)


@celery_app.task(name="reindex_knowledge_base_after_access_change", bind=True, max_retries=3)
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
