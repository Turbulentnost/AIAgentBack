from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from typing import Any

from app.agents.meeting_agent.memo_presenter import build_memo_detail
from app.core.config import settings
from app.core.logging import get_logger
from app.services.meeting_redis_ops import meeting_redis_get, meeting_redis_setex
from app.services.meeting_attendees import attendee_fio_from_detail
from app.services.meeting_agent_errors import format_onec_load_error, format_participants_missing_error
from app.tools.onec.connection import CONFIG, create_session

logger = get_logger(__name__)

_CACHE_KEY_PREFIX = "meeting:memo"
_DASHBOARD_KEY_PREFIX = "meeting:dashboard"


class MemoCacheMissError(LookupError):
    """Детали СЗ отсутствуют в Redis; 1С вызывается только при прогреве dashboard."""


def _cache_key(ref_key: str) -> str:
    return f"{_CACHE_KEY_PREFIX}:{ref_key.strip().lower()}"


def _dashboard_cache_key(target_date: date) -> str:
    return f"{_DASHBOARD_KEY_PREFIX}:{target_date.isoformat()}"


def _serialize_payload(payload: dict[str, Any], *, fetched_at: datetime) -> dict[str, Any]:
    return {
        "payload": payload,
        "fetched_at": fetched_at.isoformat(),
    }


def _parse_cached_payload(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    fetched_at = data.get("fetched_at")
    if isinstance(fetched_at, str):
        data["fetched_at"] = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    return data


def collect_memo_ref_keys(payload: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    ref_keys: list[str] = []
    for item in (payload.get("unapproved") or []) + (payload.get("today") or []):
        ref_key = (item.get("ref_key") or "").strip().lower()
        if ref_key and ref_key not in seen:
            seen.add(ref_key)
            ref_keys.append(ref_key)
    return ref_keys


def find_dashboard_item(payload: dict[str, Any], ref_key: str) -> dict[str, Any] | None:
    normalized = ref_key.strip().lower()
    for item in (payload.get("unapproved") or []) + (payload.get("today") or []):
        item_ref = (item.get("ref_key") or "").strip().lower()
        if item_ref == normalized:
            return item
    return None


def build_detail_from_dashboard_item(item: dict[str, Any]) -> dict[str, Any]:
    """Сводный detail из карточки dashboard (без полной шапки 1С)."""
    queue = dict(item)
    participant_names = [
        name.strip()
        for name in (item.get("participant_names") or [])
        if isinstance(name, str) and name.strip()
    ]
    participants = [
        {"full_name": name, "ref_key": None, "department": None}
        for name in participant_names
    ]
    participants_count = max(item.get("participants_count") or 0, len(participants))
    return {
        "ref_key": item.get("ref_key"),
        "number": item.get("number"),
        "title": item.get("title") or item.get("subject"),
        "status": item.get("status"),
        "status_label": item.get("status_label"),
        "queue": queue,
        "application": {
            "initiator": None,
            "manager": None,
            "participants": participants,
            "participants_count": participants_count,
            "agenda": item.get("subject") or item.get("title"),
            "scheduled_label": item.get("scheduled_label"),
            "document_date": item.get("document_date"),
            "meeting_start": item.get("meeting_start"),
            "meeting_end": item.get("meeting_end"),
            "duration_minutes": None,
            "location": item.get("location"),
            "meeting_type": item.get("meeting_type"),
            "meeting_type_label": item.get("meeting_type_label"),
            "priority": None,
        },
        "validation_checks": [],
        "warnings": item.get("warnings") or [],
        "history": [],
        "agent_recommendation": None,
    }


def detail_is_agent_ready(detail: dict[str, Any]) -> bool:
    """True для полного detail из build_memo_detail, False для dashboard-fallback."""
    application = detail.get("application") or {}
    if application.get("initiator") or application.get("manager"):
        return True
    for person in application.get("participants") or []:
        if not isinstance(person, dict):
            continue
        if person.get("ref_key") or person.get("email"):
            return True
    return False


def detail_to_memo_document(detail: dict[str, Any]) -> dict[str, Any]:
    """Преобразует кэшированный detail в структуру документа для MeetingBackend."""
    app = detail.get("application") or {}
    queue = dict(detail.get("queue") or {})
    if app.get("meeting_start"):
        queue["ВремяНачалаСовещания"] = app.get("meeting_start")
    if app.get("meeting_end"):
        queue["ВремяОкончанияСовещания"] = app.get("meeting_end")
    if queue.get("desired_meeting_date"):
        queue["ЖелаемаяДатаПроведенияСовещания"] = queue.get("desired_meeting_date")

    participants = [{"ФИО": name} for name in attendee_fio_from_detail(detail)]
    return {
        "memo": {
            "Ref_Key": detail.get("ref_key"),
            "Number": detail.get("number"),
            "Date": app.get("document_date"),
            "ВидСовещания": app.get("meeting_type"),
            "ВремяНачалаСовещания": app.get("meeting_start"),
            "ВремяОкончанияСовещания": app.get("meeting_end"),
            "Комментарий": detail.get("title") or app.get("agenda"),
        },
        "header": queue,
        "participants": participants,
    }


class MeetingMemoCacheService:
    async def get_memo_detail(
        self,
        ref_key: str,
        *,
        target_date: date | None = None,
        force_refresh: bool = False,
    ) -> tuple[dict[str, Any], datetime, bool]:
        normalized = ref_key.strip().lower()
        if force_refresh:
            payload, fetched_at = await self._fetch_and_store(normalized)
            return payload, fetched_at, False

        if not settings.MEETING_DASHBOARD_CACHE_ENABLED:
            raise MemoCacheMissError(
                "Кэш СЗ отключён. Включите MEETING_DASHBOARD_CACHE_ENABLED или обновите dashboard."
            )

        cached = await self._read_cache(normalized)
        if cached is not None:
            return cached["payload"], cached["fetched_at"], True

        fallback = await self._read_detail_from_dashboard_cache(normalized, target_date=target_date)
        if fallback is not None:
            payload, fetched_at = fallback
            logger.info("meeting_memo_cache_dashboard_fallback", ref_key=normalized)
            return payload, fetched_at, True

        raise MemoCacheMissError(
            "Детали служебной записки не найдены в кэше. "
            "Обновите dashboard или дождитесь прогрева в 10:00/15:00."
        )

    async def get_memo_detail_for_agent(
        self,
        ref_key: str,
    ) -> tuple[dict[str, Any], datetime, bool]:
        """Полный detail для slot-preview: memo-кэш или загрузка из 1С (без dashboard-fallback)."""
        normalized = ref_key.strip().lower()

        if not settings.MEETING_DASHBOARD_CACHE_ENABLED:
            raise MemoCacheMissError(
                "Кэш СЗ отключён. Включите MEETING_DASHBOARD_CACHE_ENABLED или обновите dashboard."
            )

        cached = await self._read_cache(normalized)
        if cached is not None and detail_is_agent_ready(cached["payload"]):
            return cached["payload"], cached["fetched_at"], True

        logger.info(
            "meeting_memo_agent_fetch",
            ref_key=normalized,
            reason="cache_miss_or_incomplete",
        )
        try:
            payload, fetched_at = await self._fetch_and_store(normalized)
        except Exception as exc:
            raise MemoCacheMissError(format_onec_load_error(exc)) from exc

        if not detail_is_agent_ready(payload):
            raise MemoCacheMissError(format_participants_missing_error())

        return payload, fetched_at, False

    async def _read_detail_from_dashboard_cache(
        self,
        ref_key: str,
        *,
        target_date: date | None = None,
    ) -> tuple[dict[str, Any], datetime] | None:
        day = target_date or date.today()
        try:
            raw = await meeting_redis_get(_dashboard_cache_key(day))
        except Exception as exc:
            logger.warning(
                "meeting_memo_dashboard_fallback_read_failed",
                ref_key=ref_key,
                error=str(exc),
            )
            return None
        if not raw:
            return None
        try:
            dashboard = _parse_cached_payload(raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            return None
        item = find_dashboard_item(dashboard, ref_key)
        if item is None:
            return None
        fetched_at = dashboard.get("fetched_at")
        if not isinstance(fetched_at, datetime):
            fetched_at = datetime.now(timezone.utc)
        return build_detail_from_dashboard_item(item), fetched_at

    async def _fetch_and_store(self, ref_key: str) -> tuple[dict[str, Any], datetime]:
        fetched_at = datetime.now(timezone.utc)
        payload = await asyncio.to_thread(
            build_memo_detail,
            create_session(CONFIG),
            CONFIG,
            ref_key,
        )
        if settings.MEETING_DASHBOARD_CACHE_ENABLED:
            await self._write_cache(ref_key, payload, fetched_at=fetched_at)
        return payload, fetched_at

    async def _read_cache(self, ref_key: str) -> dict[str, Any] | None:
        try:
            raw = await meeting_redis_get(_cache_key(ref_key))
        except Exception as exc:
            logger.warning(
                "meeting_memo_cache_read_failed",
                ref_key=ref_key,
                error=str(exc),
            )
            return None
        if not raw:
            return None
        try:
            return _parse_cached_payload(raw)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("meeting_memo_cache_invalid", ref_key=ref_key, error=str(exc))
            return None

    async def _write_cache(
        self,
        ref_key: str,
        payload: dict[str, Any],
        *,
        fetched_at: datetime,
    ) -> None:
        try:
            await meeting_redis_setex(
                _cache_key(ref_key),
                settings.MEETING_DASHBOARD_CACHE_TTL_SECONDS,
                json.dumps(_serialize_payload(payload, fetched_at=fetched_at), ensure_ascii=False, default=str),
            )
        except Exception as exc:
            logger.warning(
                "meeting_memo_cache_write_failed",
                ref_key=ref_key,
                error=str(exc),
            )


async def warm_memo_details_from_dashboard(payload: dict[str, Any]) -> None:
    """Загружает detail всех СЗ из dashboard в Redis. Вызывается только после чтения из 1С."""
    if not settings.MEETING_DASHBOARD_CACHE_ENABLED:
        return
    ref_keys = collect_memo_ref_keys(payload)
    if not ref_keys:
        return
    service = MeetingMemoCacheService()
    for ref_key in ref_keys:
        try:
            await service._fetch_and_store(ref_key)
        except Exception as exc:
            logger.warning(
                "meeting_memo_cache_warm_failed",
                ref_key=ref_key,
                error=str(exc),
            )
    logger.info("meeting_memo_cache_warmed", count=len(ref_keys))
