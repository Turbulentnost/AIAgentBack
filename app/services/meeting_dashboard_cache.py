from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.logging import get_logger
from app.services.meeting_memo_cache import patch_dashboard_payload_slot, patch_dashboard_payload_status
from app.services.meeting_memo_cache import _dashboard_items
from app.services.meeting_redis_ops import meeting_redis_get, meeting_redis_setex

logger = get_logger(__name__)

_CACHE_KEY_PREFIX = "meeting:dashboard"


def _cache_key(target_date: date) -> str:
    return f"{_CACHE_KEY_PREFIX}:{target_date.isoformat()}"


def _serialize_payload(payload: dict[str, Any], *, fetched_at: datetime, fetch_ok: bool = True) -> dict[str, Any]:
    return {
        **payload,
        "fetched_at": fetched_at.isoformat(),
        "fetch_ok": fetch_ok,
    }


def _payload_from_cached(cached: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in cached.items()
        if key not in {"fetched_at", "fetch_ok"}
    }


def _repair_cached_payload(payload: dict[str, Any], day: date) -> dict[str, Any]:
    repaired = dict(payload)
    repaired.setdefault("date", day.isoformat())
    repaired.setdefault("unapproved", [])
    repaired.setdefault("today", [])
    repaired.setdefault("counts", {})
    if not repaired.get("items"):
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for group in (repaired.get("unapproved") or [], repaired.get("today") or []):
            for item in group:
                ref_key = str(item.get("ref_key") or "").strip()
                key = ref_key or f"number:{item.get('number')}"
                if key not in merged:
                    order.append(key)
                merged[key] = item
        repaired["items"] = [merged[key] for key in order]
    return repaired


def _cache_has_queue_people_schema(cached: dict[str, Any]) -> bool:
    payload = _payload_from_cached(cached)
    items = _dashboard_items(payload)
    if not items:
        return True
    return all("initiator" in item and "manager" in item for item in items)


def _is_usable_cache(cached: dict[str, Any]) -> bool:
    if not _cache_has_queue_people_schema(cached):
        return False
    if cached.get("fetch_ok") is True:
        return True
    counts = cached.get("counts") or {}
    return bool(counts.get("unapproved") or counts.get("today") or counts.get("items"))


def _parse_cached_payload(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    fetched_at = data.get("fetched_at")
    if isinstance(fetched_at, str):
        data["fetched_at"] = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    return data


def business_date() -> date:
    tz = ZoneInfo(settings.MEETING_DASHBOARD_CACHE_WARMUP_TIMEZONE)
    return datetime.now(tz).date()


class MeetingDashboardCacheService:
    """Кэш dashboard СЗ в Redis.

    1С вызывается только если:
      1. нет кэша за сегодня (первый GET /meetings/dashboard);
      2. Celery-прогрев в 10:00 и 15:00;
      3. POST /meetings/dashboard/refresh (кнопка «Обновить»).
    GET /meetings/dashboard — только Redis (не путать с F5 и не использовать для принудительного refresh).
    """

    async def get_dashboard(
        self,
        *,
        target_date: date | None = None,
        force_refresh: bool = False,
    ) -> tuple[dict[str, Any], datetime, bool]:
        """Возвращает payload, fetched_at и признак чтения из кэша."""
        day = target_date or date.today()
        if settings.MEETING_DASHBOARD_CACHE_ENABLED and not force_refresh:
            cached = await self._read_cache(day)
            if cached is not None and _is_usable_cache(cached):
                fetched_at = cached["fetched_at"]
                payload = _repair_cached_payload(_payload_from_cached(cached), day)
                return payload, fetched_at, True

        payload, fetched_at = await self._fetch_and_store(day)
        return payload, fetched_at, False

    async def refresh_dashboard(
        self,
        *,
        target_date: date | None = None,
    ) -> tuple[dict[str, Any], datetime, bool, str | None]:
        day = target_date or date.today()
        try:
            payload, fetched_at = await self._fetch_and_store(day)
            return payload, fetched_at, False, None
        except Exception as exc:
            logger.warning("meeting_dashboard_refresh_failed", date=day.isoformat(), error=str(exc))
            cached = await self._read_cache(day)
            if cached is not None and _is_usable_cache(cached):
                fetched_at = cached["fetched_at"]
                payload = _repair_cached_payload(_payload_from_cached(cached), day)
                return payload, fetched_at, True, str(exc)
            raise

    async def warmup(self, *, target_date: date | None = None) -> dict[str, Any]:
        day = target_date or business_date()
        payload, fetched_at, from_cache, error = await self.refresh_dashboard(target_date=day)
        logger.info(
            "meeting_dashboard_cache_warmed",
            date=day.isoformat(),
            from_cache=from_cache,
            error=error,
            counts=payload.get("counts"),
        )
        return {
            "date": day.isoformat(),
            "fetched_at": fetched_at.isoformat(),
            "from_cache": from_cache,
            "error": error,
            "counts": payload.get("counts") or {},
        }

    async def _fetch_and_store(self, day: date) -> tuple[dict[str, Any], datetime]:
        from app.agents.meeting_agent.dashboard import get_meeting_dashboard
        from app.services.meeting_memo_cache import warm_memo_details_from_dashboard

        fetched_at = datetime.now(timezone.utc)
        payload = await asyncio.to_thread(get_meeting_dashboard, target_date=day)
        if settings.MEETING_DASHBOARD_CACHE_ENABLED:
            await self._write_cache(day, payload, fetched_at=fetched_at)
            try:
                # Текст СЗ дотягивается в get_meeting_dashboard и кладётся в per-memo кэш.
                await warm_memo_details_from_dashboard(payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "meeting_memo_text_warm_failed",
                    date=day.isoformat(),
                    error=str(exc),
                )
        return payload, fetched_at

    async def _read_cache(self, day: date) -> dict[str, Any] | None:
        try:
            raw = await meeting_redis_get(_cache_key(day))
        except Exception as exc:
            logger.warning(
                "meeting_dashboard_cache_read_failed",
                date=day.isoformat(),
                error=str(exc),
            )
            return None
        if not raw:
            return None
        try:
            return _parse_cached_payload(raw)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("meeting_dashboard_cache_invalid", date=day.isoformat(), error=str(exc))
            return None

    async def _write_cache(self, day: date, payload: dict[str, Any], *, fetched_at: datetime) -> None:
        try:
            await meeting_redis_setex(
                _cache_key(day),
                settings.MEETING_DASHBOARD_CACHE_TTL_SECONDS,
                json.dumps(_serialize_payload(payload, fetched_at=fetched_at), ensure_ascii=False, default=str),
            )
        except Exception as exc:
            logger.warning(
                "meeting_dashboard_cache_write_failed",
                date=day.isoformat(),
                error=str(exc),
            )

    async def patch_status(
        self,
        ref_key: str,
        status: str,
        *,
        target_date: date | None = None,
    ) -> bool:
        """Обновляет статус СЗ в dashboard-кэше без запроса в 1С."""
        if not settings.MEETING_DASHBOARD_CACHE_ENABLED:
            return False
        day = target_date or date.today()
        cached = await self._read_cache(day)
        if cached is None:
            return False
        payload = _payload_from_cached(cached)
        if not any(
            (item.get("ref_key") or "").strip().lower() == ref_key.strip().lower()
            for item in _dashboard_items(payload)
        ):
            return False
        patched_payload = patch_dashboard_payload_status(payload, ref_key, status)
        fetched_at = cached.get("fetched_at")
        if not isinstance(fetched_at, datetime):
            fetched_at = datetime.now(timezone.utc)
        await self._write_cache(day, patched_payload, fetched_at=fetched_at)
        return True

    async def patch_meeting_slot(
        self,
        ref_key: str,
        *,
        slot_start: str,
        slot_end: str,
        location: str | None = None,
        target_date: date | None = None,
    ) -> bool:
        if not settings.MEETING_DASHBOARD_CACHE_ENABLED:
            return False
        day = target_date or date.today()
        cached = await self._read_cache(day)
        if cached is None:
            return False
        payload = _payload_from_cached(cached)
        if not any(
            (item.get("ref_key") or "").strip().lower() == ref_key.strip().lower()
            for item in _dashboard_items(payload)
        ):
            return False
        patched_payload = patch_dashboard_payload_slot(
            payload,
            ref_key,
            slot_start=slot_start,
            slot_end=slot_end,
            location=location,
        )
        fetched_at = cached.get("fetched_at")
        if not isinstance(fetched_at, datetime):
            fetched_at = datetime.now(timezone.utc)
        await self._write_cache(day, patched_payload, fetched_at=fetched_at)
        return True
