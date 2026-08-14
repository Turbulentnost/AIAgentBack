"""In-process fallback для ежечасной синхронизации 1С, когда Celery Beat не запущен."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_LOCK_KEY = "onec:aveon:daily_sync:lock"
_PROGRESS_LOCK = Lock()
_STEP_TITLES = {
    "stock": "Остатки",
    "resource_specs": "Спецификации",
    "production_plan": "План производства",
    "save_stock": "Запись остатков",
    "save_specs": "Запись спецификаций",
    "save_plan": "Запись плана",
}
_SYNC_PROGRESS: dict[str, Any] = {
    "running": False,
    "owner": "",
    "started_at": None,
    "finished_at": None,
    "step": "",
    "label": "",
    "steps": [],
}


def _sync_timezone() -> ZoneInfo:
    return ZoneInfo(settings.MEETING_DASHBOARD_CACHE_WARMUP_TIMEZONE)


def _is_sync_hour(hour: int, every_hours: int) -> bool:
    return hour % every_hours == 0


def next_onec_sync_at(now: datetime | None = None) -> datetime:
    """Следующий слот синхронизации по расписанию (как Celery Beat: :00 каждый N часов)."""
    tz = _sync_timezone()
    current = (now or datetime.now(tz)).astimezone(tz)
    every_hours = max(1, int(settings.ONEC_SYNC_EVERY_HOURS))
    sync_minute = int(settings.ONEC_SYNC_MINUTE) % 60

    probe = current.replace(minute=sync_minute, second=0, microsecond=0)
    if probe < current:
        probe += timedelta(hours=1)
        probe = probe.replace(minute=sync_minute, second=0, microsecond=0)

    while not _is_sync_hour(probe.hour, every_hours):
        probe += timedelta(hours=1)
        probe = probe.replace(minute=sync_minute, second=0, microsecond=0)

    return probe


def seconds_until_next_onec_sync(now: datetime | None = None) -> float:
    tz = _sync_timezone()
    current = (now or datetime.now(tz)).astimezone(tz)
    return max(0.0, (next_onec_sync_at(current) - current).total_seconds())


def _try_acquire_sync_lock(owner: str) -> tuple[object | None, bool]:
    if not settings.REDIS_URL:
        return None, True
    try:
        from redis import Redis

        client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        acquired = bool(
            client.set(
                _LOCK_KEY,
                owner,
                nx=True,
                ex=settings.ONEC_DAILY_SYNC_LOCK_TTL_SECONDS,
            )
        )
        if not acquired:
            client.close()
            return None, False
        return client, True
    except Exception as exc:
        logger.warning("onec_sync_scheduler.lock_failed", error=str(exc))
        return None, True


def _release_sync_lock(client: object | None) -> None:
    if client is None:
        return
    try:
        client.delete(_LOCK_KEY)  # type: ignore[attr-defined]
        client.close()  # type: ignore[attr-defined]
    except Exception:
        pass


def _base_progress(owner: str) -> dict[str, Any]:
    return {
        "running": True,
        "owner": owner,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "step": "connect",
        "label": "Подключаемся к 1С и готовим выгрузку",
        "steps": [
            {"key": key, "title": title, "status": "pending", "message": ""}
            for key, title in _STEP_TITLES.items()
        ],
    }


def _set_sync_progress(payload: dict[str, Any]) -> None:
    with _PROGRESS_LOCK:
        _SYNC_PROGRESS.clear()
        _SYNC_PROGRESS.update(payload)


def _update_sync_progress(step: str, status: str, message: str | None = None) -> None:
    with _PROGRESS_LOCK:
        _SYNC_PROGRESS["running"] = True
        _SYNC_PROGRESS["step"] = step
        if message:
            _SYNC_PROGRESS["label"] = message
        for item in _SYNC_PROGRESS.get("steps", []):
            if item.get("key") == step:
                item["status"] = status
                if message:
                    item["message"] = message
                break


def _finish_sync_progress(status: str, message: str) -> None:
    with _PROGRESS_LOCK:
        _SYNC_PROGRESS["running"] = False
        _SYNC_PROGRESS["finished_at"] = datetime.now(timezone.utc).isoformat()
        _SYNC_PROGRESS["step"] = status
        _SYNC_PROGRESS["label"] = message


def get_onec_sync_progress() -> dict[str, Any]:
    with _PROGRESS_LOCK:
        return {
            **_SYNC_PROGRESS,
            "steps": [dict(item) for item in _SYNC_PROGRESS.get("steps", [])],
        }


async def run_onec_sync_with_lock(*, owner: str) -> dict:
    from app.services.onec_daily_sync import run_onec_daily_sync

    client, acquired = await asyncio.to_thread(_try_acquire_sync_lock, owner)
    if not acquired:
        return {"ok": False, "status": "skipped_locked", "owner": owner}

    _set_sync_progress(_base_progress(owner))
    try:
        result = await run_onec_daily_sync(progress=_update_sync_progress)
        result["status"] = "completed" if result.get("ok") else "failed"
        _finish_sync_progress(
            result["status"],
            "Выгрузка из 1С завершена" if result.get("ok") else "Выгрузка из 1С завершилась с ошибкой",
        )
        return result
    except Exception:
        _finish_sync_progress("failed", "Выгрузка из 1С прервана из-за ошибки")
        raise
    finally:
        await asyncio.to_thread(_release_sync_lock, client)


async def onec_sync_scheduler_loop(stop_event: asyncio.Event) -> None:
    """Синхронизация в фиксированные часы (:ONEC_SYNC_MINUTE каждые ONEC_SYNC_EVERY_HOURS, MSK)."""
    if not settings.ONEC_DAILY_SYNC_ENABLED or not settings.ONEC_INPROCESS_SYNC_ENABLED:
        logger.info(
            "onec_sync_scheduler.disabled",
            daily_sync=settings.ONEC_DAILY_SYNC_ENABLED,
            inprocess=settings.ONEC_INPROCESS_SYNC_ENABLED,
        )
        return

    tz = _sync_timezone()
    next_at = next_onec_sync_at()
    logger.info(
        "onec_sync_scheduler.started",
        timezone=settings.MEETING_DASHBOARD_CACHE_WARMUP_TIMEZONE,
        every_hours=settings.ONEC_SYNC_EVERY_HOURS,
        sync_minute=settings.ONEC_SYNC_MINUTE,
        next_run_at=next_at.isoformat(),
    )

    while not stop_event.is_set():
        sleep_seconds = seconds_until_next_onec_sync()
        if sleep_seconds > 0:
            logger.info(
                "onec_sync_scheduler.waiting",
                next_run_at=next_onec_sync_at().isoformat(),
                sleep_seconds=round(sleep_seconds),
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_seconds)
                break
            except TimeoutError:
                pass

        if stop_event.is_set():
            break

        slot_at = next_onec_sync_at()
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            result = await run_onec_sync_with_lock(owner="inprocess_scheduler")
            logger.info(
                "onec_sync_scheduler.tick",
                started_at=started_at,
                scheduled_for=slot_at.isoformat(),
                status=result.get("status"),
                ok=result.get("ok"),
            )
        except Exception as exc:
            logger.exception("onec_sync_scheduler.tick_failed", error=str(exc))

        # Не запускать повторно в ту же минуту — ждём следующий слот.
        await asyncio.sleep(61)

    logger.info("onec_sync_scheduler.stopped")
