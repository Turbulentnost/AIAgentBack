from __future__ import annotations
from typing import Any
from app.workers.celery_app import celery_app
@celery_app.task(name="run_task", bind=True, max_retries=3)
def run_task(self, task_id: str, task_type: str | None, input_payload: dict) -> dict[str, Any]:
    import asyncio
    from app.orchestrator.orchestrator import orchestrator
    return asyncio.run(orchestrator.run(task_type, input_payload))
@celery_app.task(name="index_document", bind=True, max_retries=3)
def index_document(self, document_id: str) -> dict[str, Any]:
    return {"document_id": document_id, "status": "queued"}
