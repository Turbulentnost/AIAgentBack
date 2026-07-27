"""Опрос TurboProject при открытии графика совещаний (только уведомления)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.meeting_redis_ops import meeting_redis_get, meeting_redis_setex
from app.services.turbo_project_series_sync_service import (
    TurboProjectSeriesSyncError,
    TurboProjectSeriesSyncService,
)

logger = logging.getLogger(__name__)

REDIS_COOLDOWN_KEY = "turbo_project:series_sync:cooldown"
_memory_cooldown_until = 0.0
_sync_lock = asyncio.Lock()


def _credentials_configured() -> bool:
    return bool(
        (settings.TURBO_PROJECT_API_BASE_URL or "").strip()
        and (settings.TURBO_PROJECT_EMAIL or "").strip()
        and (settings.TURBO_PROJECT_PASSWORD or "").strip()
    )


async def _cooldown_active() -> bool:
    global _memory_cooldown_until
    now = time.monotonic()
    if now < _memory_cooldown_until:
        return True
    try:
        raw = await meeting_redis_get(REDIS_COOLDOWN_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.debug("turbo_project_sync.cooldown_redis_unavailable error=%s", exc)
        return False
    return bool(raw)


async def _mark_cooldown() -> None:
    global _memory_cooldown_until
    ttl = max(int(settings.TURBO_PROJECT_SERIES_SYNC_COOLDOWN_SECONDS), 1)
    _memory_cooldown_until = time.monotonic() + ttl
    try:
        await meeting_redis_setex(REDIS_COOLDOWN_KEY, ttl, "1")
    except Exception as exc:  # noqa: BLE001
        logger.debug("turbo_project_sync.cooldown_redis_set_failed error=%s", exc)


async def maybe_sync_turbo_projects_on_schedule_open(
    db: AsyncSession,
) -> dict[str, Any] | None:
    """Detect новых проектов → in-app уведомления (без Outlook и без создания серии)."""
    if not settings.TURBO_PROJECT_SERIES_SYNC_ENABLED:
        return {"skipped": True, "reason": "sync_disabled"}
    if not settings.TURBO_PROJECT_SERIES_SYNC_ON_SCHEDULE_LIST:
        return {"skipped": True, "reason": "schedule_list_sync_disabled"}
    if not _credentials_configured():
        return {"skipped": True, "reason": "credentials_missing"}

    if _sync_lock.locked():
        return {"skipped": True, "reason": "sync_in_progress"}

    async with _sync_lock:
        if await _cooldown_active():
            return {"skipped": True, "reason": "cooldown"}

        try:
            result = await TurboProjectSeriesSyncService(db).discover_and_notify()
            await _mark_cooldown()
            summary = result.as_dict()
            logger.info(
                "turbo_project_sync.on_schedule_list cache_hit=%s notified=%s notifications=%s "
                "skipped_existing=%s failed=%s",
                summary.get("cache_hit"),
                summary.get("notified"),
                summary.get("notifications_created"),
                summary.get("skipped_existing_series"),
                summary.get("failed"),
            )
            return summary
        except TurboProjectSeriesSyncError as exc:
            await _mark_cooldown()
            logger.warning("turbo_project_sync.on_schedule_list_failed error=%s", exc)
            return {"skipped": True, "reason": "sync_error", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            await _mark_cooldown()
            logger.exception("turbo_project_sync.on_schedule_list_unexpected")
            return {"skipped": True, "reason": "unexpected_error", "error": str(exc)}
