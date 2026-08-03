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
if settings.PROCUREMENT_ORCHESTRATOR_ENABLED:
    _beat_schedule["sync-procurement-material-orders"] = {
        "task": "sync_procurement_material_orders",
        "schedule": float(settings.PROCUREMENT_ORCHESTRATOR_INTERVAL_SECONDS),
        "options": {
            "queue": "procurement_poll",
            "expires": settings.PROCUREMENT_ORCHESTRATOR_INTERVAL_SECONDS,
        },
    }
    _beat_schedule["poll-procurement-reorder-points"] = {
        "task": "poll_procurement_reorder_points",
        "schedule": float(settings.PROCUREMENT_ORCHESTRATOR_REORDER_INTERVAL_SECONDS),
        "options": {
            "queue": "procurement_poll",
            "expires": settings.PROCUREMENT_ORCHESTRATOR_REORDER_INTERVAL_SECONDS,
        },
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
if settings.SCHEDULED_MEETINGS_CARD_SYNC_ENABLED:
    _beat_schedule["sync-scheduled-meeting-registry-cards"] = {
        "task": "sync_scheduled_meeting_registry_cards",
        "schedule": crontab(
            hour=settings.SCHEDULED_MEETINGS_CARD_SYNC_HOUR,
            minute=settings.SCHEDULED_MEETINGS_CARD_SYNC_MINUTE,
        ),
        "options": {"queue": "default"},
    }
if settings.MEETING_PROTOCOL_DRAFT_ENABLED and settings.MEETING_PROTOCOL_DISPATCH_BEAT_ENABLED:
    _beat_schedule["dispatch-meeting-protocol-drafts"] = {
        "task": "dispatch_meeting_protocol_drafts",
        "schedule": crontab(
            hour=settings.MEETING_PROTOCOL_DISPATCH_BEAT_HOURS,
            minute=settings.MEETING_PROTOCOL_DISPATCH_BEAT_MINUTE,
        ),
        "options": {"queue": "default"},
    }
if settings.TURBO_PROJECT_SERIES_SYNC_ENABLED:
    _beat_schedule["sync-turbo-project-meeting-series"] = {
        "task": "sync_turbo_project_meeting_series",
        "schedule": crontab(
            hour=settings.TURBO_PROJECT_SERIES_SYNC_HOUR,
            minute=settings.TURBO_PROJECT_SERIES_SYNC_MINUTE,
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
        "sync_scheduled_meeting_registry_cards": {"queue": "default"},
        "create_registry_protocol_draft": {"queue": "default"},
        "dispatch_meeting_protocol_drafts": {"queue": "default"},
        "sync_turbo_project_meeting_series": {"queue": "default"},
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
        "poll_procurement_sources": {"queue": "procurement_poll"},
        "poll_procurement_reorder_points": {"queue": "procurement_poll"},
        "reconcile_procurement_supplier_orders": {"queue": "procurement_poll"},
        "sync_procurement_material_orders": {"queue": "procurement_poll"},
        "run_procurement_case_task": {"queue": "agents"},
    },
    task_serializer="json",
    task_track_started=True,
    timezone=settings.MEETING_DASHBOARD_CACHE_WARMUP_TIMEZONE,
    worker_prefetch_multiplier=1,
    visibility_timeout=settings.CELERY_VISIBILITY_TIMEOUT_SECONDS,
    beat_schedule=_beat_schedule,
)
