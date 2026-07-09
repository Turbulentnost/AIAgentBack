"""Celery-приложение для асинхронной обработки писем (раздел 5.1, 6 ТЗ)."""

from __future__ import annotations

from celery import Celery

from agent_pochta.config import get_settings

settings = get_settings()

celery_app = Celery("agent_pochta", broker=settings.celery_broker_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_backend="rpc://",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "agent_pochta.poll_imap": {"queue": "imap"},
    },
    beat_schedule={
        "poll-imap-mailboxes": {
            "task": "agent_pochta.poll_imap",
            "schedule": float(settings.imap_poll_interval_sec),
            "options": {"expires": settings.imap_poll_interval_sec},
        },
        "export-statistics": {
            "task": "agent_pochta.export_statistics",
            "schedule": float(settings.stats_export_interval_sec),
            "options": {"expires": settings.stats_export_interval_sec},
        },
    },
)

import agent_pochta.workers.tasks  # noqa: E402, F401 — регистрация задач

from celery.signals import worker_process_shutdown  # noqa: E402


@worker_process_shutdown.connect
def _shutdown_worker_runtime(**_kwargs) -> None:
    from agent_pochta.workers.runtime import reset_worker_runtime

    reset_worker_runtime()
