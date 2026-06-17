from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "ai_agents_platform",
    broker=settings.celery_broker(),
    backend=settings.celery_backend(),
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    enable_utc=True,
    result_expires=60 * 60 * 24,
    result_serializer="json",
    task_acks_late=True,
    task_default_queue="default",
    task_reject_on_worker_lost=True,
    task_routes={
        "debug_task": {"queue": "default"},
        "process_document": {"queue": "documents"},
        "run_agent": {"queue": "agents"},
        "index_document": {"queue": "indexing"},
        "index_knowledge_base_full": {"queue": "indexing"},
        "index_knowledge_base_source": {"queue": "indexing"},
        "reindex_knowledge_base_embeddings": {"queue": "indexing"},
        "reindex_knowledge_base_after_access_change": {"queue": "indexing"},
        "generate_report": {"queue": "reports"},
        "update_task_status": {"queue": "default"},
        "run_department_analysis": {"queue": "default"},
    },
    task_serializer="json",
    task_track_started=True,
    timezone="UTC",
    worker_prefetch_multiplier=1,
)
