from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "ai_agents_platform",
    broker=settings.celery_broker(),
    backend=settings.celery_backend(),
    include=["app.workers.tasks"],
)

_beat_schedule = {}
if settings.MEETING_DASHBOARD_CACHE_WARMUP_ENABLED:
    _beat_schedule["warm-meeting-dashboard-cache"] = {
        "task": "warm_meeting_dashboard_cache",
        "schedule": crontab(
            hour=settings.MEETING_DASHBOARD_CACHE_WARMUP_HOURS,
            minute=settings.MEETING_DASHBOARD_CACHE_WARMUP_MINUTE,
        ),
        "options": {"queue": "default"},
    }
_beat_schedule["recover-stale-knowledge-base-indexing-jobs"] = {
    "task": "recover_stale_knowledge_base_indexing_jobs",
    "schedule": settings.KB_INDEXING_RECOVERY_INTERVAL_SECONDS,
    "options": {"queue": "indexing"},
}
if settings.SCHEDULED_MEETINGS_ARCHIVE_ENABLED:
    _beat_schedule["archive-expired-scheduled-meetings"] = {
        "task": "archive_expired_scheduled_meetings",
        "schedule": crontab(
            hour=settings.SCHEDULED_MEETINGS_ARCHIVE_HOUR,
            minute=settings.SCHEDULED_MEETINGS_ARCHIVE_MINUTE,
        ),
        "options": {"queue": "default"},
    }

celery_app.conf.update(
    accept_content=["json"],
    broker_transport_options={
        "visibility_timeout": settings.CELERY_VISIBILITY_TIMEOUT_SECONDS,
    },
    broker_connection_retry_on_startup=True,
    enable_utc=True,
    result_expires=60 * 60 * 24,
    result_serializer="json",
    task_acks_late=True,
    task_default_queue="default",
    task_reject_on_worker_lost=True,
    task_routes={
        "debug_task": {"queue": "default"},
        "warm_meeting_dashboard_cache": {"queue": "default"},
        "archive_expired_scheduled_meetings": {"queue": "default"},
        "process_document": {"queue": "documents"},
        "run_agent": {"queue": "agents"},
        "index_document": {"queue": "indexing"},
        "index_knowledge_base_full": {"queue": "indexing"},
        "index_knowledge_base_source": {"queue": "indexing"},
        "reindex_knowledge_base_embeddings": {"queue": "indexing"},
        "reindex_knowledge_base_after_access_change": {"queue": "indexing"},
        "recover_stale_knowledge_base_indexing_jobs": {"queue": "indexing"},
        "generate_report": {"queue": "reports"},
        "update_task_status": {"queue": "default"},
        "run_department_analysis": {"queue": "default"},
    },
    task_serializer="json",
    task_track_started=True,
    timezone=settings.MEETING_DASHBOARD_CACHE_WARMUP_TIMEZONE,
    worker_prefetch_multiplier=1,
    visibility_timeout=settings.CELERY_VISIBILITY_TIMEOUT_SECONDS,
    beat_schedule=_beat_schedule,
)
