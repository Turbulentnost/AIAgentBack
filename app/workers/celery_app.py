from __future__ import annotations
from celery import Celery
from app.core.config import settings
celery_app = Celery("ai_agents_platform", broker=settings.celery_broker(), backend=settings.celery_backend(), include=["app.workers.tasks"])
celery_app.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"], timezone="UTC", enable_utc=True, task_track_started=True)
