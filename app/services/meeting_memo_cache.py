from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from typing import Any

from app.agents.meeting_agent.memo_presenter import build_memo_detail
from app.tools.onec.service_memo_shared import UNAPPROVED_STATUS
from app.core.config import settings
from app.core.logging import get_logger
from app.services.meeting_redis_ops import meeting_redis_get, meeting_redis_setex
from app.services.meeting_attendees import attendee_fio_from_detail
from app.services.meeting_psd_level import (
    append_psd_level_participant_names,
    append_psd_level_participants,
    is_psd_level_header,
)
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


def _dashboard_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("items"):
        return payload.get("items") or []
    return (payload.get("unapproved") or []) + (payload.get("today") or [])


def collect_memo_ref_keys(payload: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    ref_keys: list[str] = []
    for item in _dashboard_items(payload):
        ref_key = (item.get("ref_key") or "").strip().lower()
        if ref_key and ref_key not in seen:
            seen.add(ref_key)
            ref_keys.append(ref_key)
    return ref_keys


def find_dashboard_item(payload: dict[str, Any], ref_key: str) -> dict[str, Any] | None:
    normalized = ref_key.strip().lower()
    for item in _dashboard_items(payload):
        item_ref = (item.get("ref_key") or "").strip().lower()
        if item_ref == normalized:
            return item
    return None


def _memo_status_label(status: str) -> str:
    from app.agents.meeting_agent.memo_presenter import _status_label

    return _status_label(status) or status


def _item_ref_key(item: dict[str, Any]) -> str:
    return (item.get("ref_key") or "").strip().lower()


def _apply_status_to_item(item: dict[str, Any], status: str) -> dict[str, Any]:
    status_label = _memo_status_label(status)
    patched = dict(item)
    patched["status"] = status
    patched["status_label"] = status_label
    return patched


def _merge_dashboard_item_groups(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for group in groups:
        for item in group:
            ref_key = str(item.get("ref_key") or "").strip()
            key = ref_key or f"number:{item.get('number')}"
            if key not in merged:
                order.append(key)
            merged[key] = item
    return [merged[key] for key in order]


def patch_detail_status(detail: dict[str, Any], status: str) -> dict[str, Any]:
    status_label = _memo_status_label(status)
    patched = dict(detail)
    patched["status"] = status
    patched["status_label"] = status_label
    queue = dict(patched.get("queue") or {})
    queue["status"] = status
    queue["status_label"] = status_label
    patched["queue"] = queue
    return patched


def patch_dashboard_payload_status(payload: dict[str, Any], ref_key: str, status: str) -> dict[str, Any]:
    normalized = ref_key.strip().lower()

    def patch_list(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            _apply_status_to_item(item, status) if _item_ref_key(item) == normalized else item
            for item in items
        ]

    unapproved = list(payload.get("unapproved") or [])
    today = patch_list(list(payload.get("today") or []))
    if status == UNAPPROVED_STATUS:
        unapproved = patch_list(unapproved)
    else:
        unapproved = [item for item in unapproved if _item_ref_key(item) != normalized]

    items = _merge_dashboard_item_groups(unapproved, today)
    return {
        **payload,
        "unapproved": unapproved,
        "today": today,
        "items": items,
        "counts": {
            "unapproved": len(unapproved),
            "today": len(today),
            "items": len(items),
        },
    }


def build_detail_from_dashboard_item(item: dict[str, Any]) -> dict[str, Any]:
    """Сводный detail из карточки dashboard (без полной шапки 1С)."""
    queue = dict(item)
    psd_level = bool(item.get("psd_level")) or is_psd_level_header(item)
    initiator_name = str((item.get("initiator") or {}).get("full_name") or "").strip()
    manager_name = str((item.get("manager") or {}).get("full_name") or "").strip()
    participant_names = append_psd_level_participant_names(
        [
            name.strip()
            for name in (item.get("participant_names") or [])
            if isinstance(name, str) and name.strip()
        ],
        psd_level=psd_level,
    )
    participants = append_psd_level_participants(
        [
            {"full_name": name, "ref_key": None, "department": None}
            for name in participant_names
            if name not in {initiator_name, manager_name}
        ],
        psd_level=psd_level,
    )
    participants_count = max(item.get("participants_count") or 0, len(participants))
    return {
        "ref_key": item.get("ref_key"),
        "number": item.get("number"),
        "title": item.get("title") or item.get("subject"),
        "status": item.get("status"),
        "status_label": item.get("status_label"),
        "queue": queue,
        "application": {
            "initiator": item.get("initiator"),
            "manager": item.get("manager"),
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
            "psd_level": psd_level,
        },
        "validation_checks": [],
        "warnings": item.get("warnings") or [],
        "history": [],
        "agent_recommendation": None,
        "sto_ready": False,
        "auto_approve_allowed": False,
        "sto_issues": [],
        "sto_checklist": [],
    }


def detail_has_sto_evaluation(detail: dict[str, Any]) -> bool:
    """True, если detail уже прошёл проверку СТО (первое открытие карточки)."""
    checklist = detail.get("sto_checklist")
    return isinstance(checklist, list) and len(checklist) > 0


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
        """Детали СЗ: из Redis или первая загрузка из 1С с проверкой СТО."""
        del target_date
        normalized = ref_key.strip().lower()
        if force_refresh:
            payload, fetched_at = await self._fetch_and_store(normalized)
            return payload, fetched_at, False

        if not settings.MEETING_DASHBOARD_CACHE_ENABLED:
            raise MemoCacheMissError(
                "Кэш СЗ отключён. Включите MEETING_DASHBOARD_CACHE_ENABLED или обновите dashboard."
            )

        cached = await self._read_cache(normalized)
        if cached is not None and detail_has_sto_evaluation(cached["payload"]):
            return cached["payload"], cached["fetched_at"], True

        logger.info("meeting_memo_detail_first_open", ref_key=normalized)
        try:
            payload, fetched_at = await self._fetch_and_store(normalized)
        except Exception as exc:
            raise MemoCacheMissError(
                f"Не удалось загрузить служебную записку из 1С: {exc}"
            ) from exc
        return payload, fetched_at, False

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
        if (
            cached is not None
            and detail_is_agent_ready(cached["payload"])
            and detail_has_sto_evaluation(cached["payload"])
        ):
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

    async def read_cached(self, ref_key: str) -> dict[str, Any] | None:
        """Возвращает кэшированный detail СЗ или None."""
        if not settings.MEETING_DASHBOARD_CACHE_ENABLED:
            return None
        return await self._read_cache(ref_key.strip().lower())

    async def patch_status(
        self,
        ref_key: str,
        status: str,
        *,
        history_message: str | None = None,
    ) -> bool:
        """Обновляет статус СЗ в memo-кэше без запроса в 1С."""
        if not settings.MEETING_DASHBOARD_CACHE_ENABLED:
            return False
        normalized = ref_key.strip().lower()
        cached = await self._read_cache(normalized)
        if cached is None:
            return False
        patched = patch_detail_status(cached["payload"], status)
        if history_message:
            from app.services.meeting_offline_cache import append_detail_history

            patched = append_detail_history(patched, history_message)
        await self._write_cache(normalized, patched, fetched_at=cached["fetched_at"])
        return True


async def warm_memo_details_from_dashboard(payload: dict[str, Any]) -> None:
    """Устарело: детали и проверка СТО загружаются при первом открытии карточки."""
    del payload
