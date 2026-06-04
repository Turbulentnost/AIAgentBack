from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.workers.celery_app import celery_app


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
