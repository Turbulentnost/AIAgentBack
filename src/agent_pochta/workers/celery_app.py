"""Celery-приложение для асинхронной обработки писем (раздел 5.1, 6 ТЗ).

Hardening (anti-hang):
- task_acks_late + prefetch=1 — не hoard очередь; при краше redelivery
- soft/hard time limits — один зависший HTTP/LLM не держит слот вечно
- max_tasks_per_child — периодический recycle процессов (утечки/залипший httpx)
- task_ignore_result — без rpc:// reply-очередей (мусор в RabbitMQ)
- reject_on_worker_lost — потерянный child не оставляет «призрачные» unacks
"""

from __future__ import annotations

from celery import Celery

from agent_pochta.config import get_settings

settings = get_settings()

# Soft: задача бросает SoftTimeLimitExceeded (можно перехватить).
# Hard: SIGKILL child — запас над LLM httpx timeout (120s) × несколько вызовов.
_TASK_SOFT_TIME_LIMIT_SEC = 600
_TASK_HARD_TIME_LIMIT_SEC = 720

celery_app = Celery("agent_pochta", broker=settings.celery_broker_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Результаты не читаются API (только task.id) — rpc:// копил orphan reply-очереди.
    task_ignore_result=True,
    result_backend=None,
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=_TASK_SOFT_TIME_LIMIT_SEC,
    task_time_limit=_TASK_HARD_TIME_LIMIT_SEC,
    worker_max_tasks_per_child=40,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    task_routes={
        "agent_pochta.poll_imap": {"queue": "imap"},
        "agent_pochta.retry_erp": {"queue": "erp"},
        "agent_pochta.sync_erp_correction": {"queue": "erp"},
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
        "sync-rag-to-qdrant": {
            "task": "agent_pochta.sync_rag_to_qdrant",
            "schedule": float(settings.rag_sync_interval_sec),
            "options": {"expires": settings.rag_sync_interval_sec},
        },
    },
)

import agent_pochta.workers.tasks  # noqa: E402, F401 — регистрация задач

from celery.signals import worker_process_shutdown  # noqa: E402


@worker_process_shutdown.connect
def _shutdown_worker_runtime(**_kwargs) -> None:
    from agent_pochta.workers.runtime import reset_worker_runtime

    reset_worker_runtime()
