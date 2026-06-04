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
    from app.services.document_processing.parsers.pdf_parser import PdfParsingError, PdfParsingService

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            try:
                result = await PdfParsingService(db).parse_document(document_id=uuid.UUID(document_id))
                await db.commit()
                return {
                    "celery_task_id": self.request.id,
                    "task_name": "process_document",
                    "document_id": str(result.document_id),
                    "document_version_id": str(result.document_version_id),
                    "status": "completed" if not result.failed_pages else "partial",
                    "pages_count": result.pages_count,
                    "characters_count": result.characters_count,
                    "extraction_method": result.extraction_method,
                    "requires_ocr": result.requires_ocr,
                    "ocr_used": result.ocr_used,
                    "failed_pages": result.failed_pages,
                    "extracted_text_object_name": result.extracted_text_object_name,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            except PdfParsingError as exc:
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
    return _placeholder_result(self.request.id, "index_document", document_id=document_id)


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
