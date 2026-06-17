from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from redis.asyncio import Redis

from app.core.config import settings
from app.core.logging import get_logger

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


def _is_usable_cache(cached: dict[str, Any]) -> bool:
    if cached.get("fetch_ok") is True:
        return True
    counts = cached.get("counts") or {}
    return bool(counts.get("unapproved") or counts.get("today"))


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
                return _payload_from_cached(cached), fetched_at, True

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
                return _payload_from_cached(cached), fetched_at, True, str(exc)
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

        fetched_at = datetime.now(timezone.utc)
        payload = await asyncio.to_thread(get_meeting_dashboard, target_date=day)
        if settings.MEETING_DASHBOARD_CACHE_ENABLED:
            await self._write_cache(day, payload, fetched_at=fetched_at)
        return payload, fetched_at

    async def _read_cache(self, day: date) -> dict[str, Any] | None:
        client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            raw = await client.get(_cache_key(day))
        finally:
            await client.aclose()
        if not raw:
            return None
        try:
            return _parse_cached_payload(raw)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("meeting_dashboard_cache_invalid", date=day.isoformat(), error=str(exc))
            return None

    async def _write_cache(self, day: date, payload: dict[str, Any], *, fetched_at: datetime) -> None:
        client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await client.setex(
                _cache_key(day),
                settings.MEETING_DASHBOARD_CACHE_TTL_SECONDS,
                json.dumps(_serialize_payload(payload, fetched_at=fetched_at), ensure_ascii=False, default=str),
            )
        finally:
            await client.aclose()
