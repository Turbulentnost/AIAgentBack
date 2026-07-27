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
from app.services.meeting_memo_document import (
    clean_text,
    extract_memo_text,
    format_document_date_label,
    parse_odata_datetime,
    resolve_meeting_schedule,
    schedule_duration_minutes,
)
from app.services.meeting_slot import format_slot_label, slot_duration_minutes
from app.services.meeting_psd_level import (
    append_psd_level_participant_names,
    append_psd_level_participants,
    is_psd_level_header,
)
from app.services.meeting_agent_errors import format_onec_load_error, format_participants_missing_error
from app.tools.onec.connection import CONFIG, create_session
from app.tools.onec.get_meetings import fetch_document_header, meeting_theme

logger = get_logger(__name__)

_CACHE_KEY_PREFIX = "meeting:memo"
_DASHBOARD_KEY_PREFIX = "meeting:dashboard"


class MemoCacheMissError(LookupError):
    """Детали СЗ отсутствуют в Redis; 1С вызывается только при прогреве dashboard."""


def _cache_key(ref_key: str) -> str:
    return f"{_CACHE_KEY_PREFIX}:{ref_key.strip().lower()}"


def _series_mode_cache_key(ref_key: str) -> str:
    return f"{_CACHE_KEY_PREFIX}:{ref_key.strip().lower()}:series_mode"


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


def patch_detail_schedule(
    detail: dict[str, Any],
    *,
    slot_start: str,
    slot_end: str,
    location: str | None = None,
) -> dict[str, Any]:
    patched = dict(detail)
    app = dict(patched.get("application") or {})
    queue = dict(patched.get("queue") or {})
    app["meeting_start"] = slot_start
    app["meeting_end"] = slot_end
    app["duration_minutes"] = slot_duration_minutes(slot_start, slot_end)
    if location:
        app["location"] = location
    queue["meeting_start"] = slot_start
    queue["meeting_end"] = slot_end
    queue["ВремяНачалаСовещания"] = slot_start
    queue["ВремяОкончанияСовещания"] = slot_end
    if location:
        queue["location"] = location
        queue["МестоПроведенияСовещания"] = location
    patched["application"] = app
    patched["queue"] = queue
    patched["scheduled_label"] = format_slot_label(slot_start, slot_end)
    return patched


def patch_dashboard_payload_slot(
    payload: dict[str, Any],
    ref_key: str,
    *,
    slot_start: str,
    slot_end: str,
    location: str | None = None,
) -> dict[str, Any]:
    normalized = ref_key.strip().lower()
    scheduled_label = format_slot_label(slot_start, slot_end)

    def patch_list(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        patched_items: list[dict[str, Any]] = []
        for item in items:
            if _item_ref_key(item) != normalized:
                patched_items.append(item)
                continue
            updated = dict(item)
            updated["meeting_start"] = slot_start
            updated["meeting_end"] = slot_end
            updated["scheduled_label"] = scheduled_label
            updated["ВремяНачалаСовещания"] = slot_start
            updated["ВремяОкончанияСовещания"] = slot_end
            if location:
                updated["location"] = location
                updated["МестоПроведенияСовещания"] = location
            patched_items.append(updated)
        return patched_items

    unapproved = patch_list(list(payload.get("unapproved") or []))
    today = patch_list(list(payload.get("today") or []))
    items = _merge_dashboard_item_groups(unapproved, today)
    return {
        **payload,
        "unapproved": unapproved,
        "today": today,
        "items": items,
    }


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


def _pick_text(*values: Any) -> str | None:
    for value in values:
        text = clean_text(value)
        if text:
            return text
    return None


def _location_text(raw: Any) -> str | None:
    if isinstance(raw, dict):
        return clean_text(raw.get("Description"))
    return clean_text(raw)


def _format_document_date_label(value: str | None) -> str | None:
    return format_document_date_label(value)


def _enrich_cached_header(queue: dict[str, Any], app: dict[str, Any]) -> dict[str, Any]:
    """Собирает шапку 1С из полей dashboard-кэша для валидации СТО."""
    header = dict(queue)

    date_value = _pick_text(header.get("document_date"), header.get("Date"), app.get("document_date"))
    if date_value:
        header["Date"] = date_value
        header["document_date"] = date_value
        header["document_date_label"] = _format_document_date_label(date_value)

    location = _pick_text(
        _location_text(header.get("МестоПроведенияСовещания")),
        header.get("location"),
        app.get("location"),
    )
    if location:
        header["МестоПроведенияСовещания"] = location
        header["location"] = location

    theme = _pick_text(
        header.get("ТемаСовещания"),
        header.get("subject"),
        header.get("title"),
        app.get("agenda"),
    )
    if theme:
        header["ТемаСовещания"] = theme

    memo_text = extract_memo_text(header, application=app)
    if memo_text:
        header["ТекстСлужебнойЗаписки"] = memo_text

    if app.get("meeting_start"):
        header["ВремяНачалаСовещания"] = app["meeting_start"]
    if app.get("meeting_end"):
        header["ВремяОкончанияСовещания"] = app["meeting_end"]

    desired = _pick_text(
        header.get("desired_meeting_date"),
        header.get("ЖелаемаяДатаПроведенияСовещания"),
    )
    if desired:
        header["ЖелаемаяДатаПроведенияСовещания"] = desired
        header["desired_meeting_date"] = desired

    meeting_date = _pick_text(header.get("meeting_date"), header.get("ДатаПроведенияСовещания"))
    if meeting_date:
        header["ДатаПроведенияСовещания"] = meeting_date

    if not header.get("ТемаСлужебнойЗаписки"):
        header["ТемаСлужебнойЗаписки"] = meeting_theme()

    priority = app.get("priority")
    if priority and not _pick_text(header.get("Приоритет")):
        header["Приоритет"] = priority

    if not header.get("СписокУчастников") and header.get("participant_names"):
        header["СписокУчастников"] = [
            {"Участник": name}
            for name in header["participant_names"]
            if isinstance(name, str) and name.strip()
        ]

    return header


def _sync_detail_display_fields(detail: dict[str, Any], header: dict[str, Any]) -> dict[str, Any]:
    """Синхронизирует queue/application после пересчёта СТО."""
    updated = dict(detail)
    queue = dict(updated.get("queue") or {})
    queue.update({key: value for key, value in header.items() if value is not None})

    app = dict(updated.get("application") or {})
    if header.get("document_date"):
        app["document_date"] = header["document_date"]
    if header.get("document_date_label"):
        app["document_date_label"] = header["document_date_label"]
    if header.get("location"):
        app["location"] = header["location"]
    if header.get("ТемаСовещания"):
        app["agenda"] = header["ТемаСовещания"]
    memo_text = extract_memo_text(header, queue=queue, application=app)
    if memo_text:
        app["memo_text"] = memo_text
    start, end = resolve_meeting_schedule(header)
    if start is not None:
        app["meeting_start"] = start.isoformat()
    if end is not None:
        app["meeting_end"] = end.isoformat()
    duration = schedule_duration_minutes(start, end)
    if duration is not None:
        app["duration_minutes"] = duration

    updated["queue"] = queue
    updated["application"] = app
    if header.get("document_date"):
        updated["document_date"] = header["document_date"]
    if header.get("document_date_label"):
        updated["document_date_label"] = header["document_date_label"]
    return updated


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
    document_date = _pick_text(item.get("document_date"), item.get("Date"))
    document_date_label = _format_document_date_label(document_date)
    location = _pick_text(
        _location_text(item.get("МестоПроведенияСовещания")),
        item.get("location"),
    )
    start, end = resolve_meeting_schedule(item)
    duration = schedule_duration_minutes(start, end)
    return {
        "ref_key": item.get("ref_key"),
        "number": item.get("number"),
        "title": item.get("title") or item.get("subject"),
        "status": item.get("status"),
        "status_label": item.get("status_label"),
        "document_date": document_date,
        "document_date_label": document_date_label,
        "queue": queue,
        "application": {
            "initiator": item.get("initiator"),
            "manager": item.get("manager"),
            "participants": participants,
            "participants_count": participants_count,
            "agenda": item.get("subject") or item.get("title") or item.get("ТемаСовещания"),
            "memo_text": extract_memo_text(item),
            "scheduled_label": item.get("scheduled_label"),
            "document_date": document_date,
            "document_date_label": document_date_label,
            "meeting_start": item.get("meeting_start") or (start.isoformat() if start else None),
            "meeting_end": item.get("meeting_end") or (end.isoformat() if end else None),
            "duration_minutes": duration,
            "location": location,
            "meeting_type": item.get("meeting_type"),
            "meeting_type_label": item.get("meeting_type_label"),
            "priority": item.get("priority") or _pick_text(item.get("Приоритет")),
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


def document_from_cached_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Собирает документ 1С из кэшированного detail для пересчёта СТО."""
    app = detail.get("application") or {}
    header = _enrich_cached_header(dict(detail.get("queue") or {}), app)
    memo_text = extract_memo_text(header, queue=detail.get("queue"), application=app)
    if memo_text:
        header["ТекстСлужебнойЗаписки"] = memo_text

    participants = [{"ФИО": name} for name in attendee_fio_from_detail(detail)]
    return {
        "memo": header,
        "header": header,
        "application": app,
        "participants": participants,
    }


def refresh_series_planning(
    detail: dict[str, Any],
    *,
    selected_mode: str | None = None,
) -> dict[str, Any]:
    from app.agents.meeting_agent.memo_presenter import (
        _apply_series_fields_to_queue,
        _build_series_planning,
    )

    document = document_from_cached_detail(detail)
    header = document.get("header") or {}
    series_planning = _build_series_planning(
        header,
        document,
        selected_mode=selected_mode,
    )
    detail["series_planning"] = series_planning
    queue = detail.get("queue")
    if isinstance(queue, dict):
        _apply_series_fields_to_queue(queue, series_planning)
    return series_planning


async def refresh_series_planning_async(
    detail: dict[str, Any],
    *,
    ref_key: str | None = None,
    selected_mode: str | None = None,
) -> dict[str, Any]:
    from app.agents.meeting_agent.memo_presenter import (
        _apply_series_fields_to_queue,
    )
    from app.services.meeting_memo_series_llm import build_series_planning_read_async

    document = document_from_cached_detail(detail)
    header = document.get("header") or {}
    normalized_ref = (
        ref_key or detail.get("ref_key") or clean_text(header.get("Ref_Key")) or ""
    ).strip().lower() or None
    series_planning = await build_series_planning_read_async(
        header,
        document,
        ref_key=normalized_ref,
        selected_mode=selected_mode,
    )
    detail["series_planning"] = series_planning
    queue = detail.get("queue")
    if isinstance(queue, dict):
        _apply_series_fields_to_queue(queue, series_planning)
    return series_planning


def refresh_cached_detail_assessment(
    detail: dict[str, Any],
    *,
    include_series_planning: bool = True,
) -> dict[str, Any]:
    """Пересчитывает чек-лист СТО и связанные поля по актуальным правилам."""
    from app.agents.meeting_agent.memo_presenter import _build_validation_checks, _build_warnings
    from app.agents.meeting_agent.memo_validation import build_sto_payload

    document = document_from_cached_detail(detail)
    participants_count = int((detail.get("application") or {}).get("participants_count") or 0)
    sto = build_sto_payload(document)
    header = document.get("header") or {}
    updated = _sync_detail_display_fields(detail, header)
    updated["sto_checklist"] = sto["sto_checklist"]
    updated["sto_issues"] = sto["sto_issues"]
    updated["sto_ready"] = sto["sto_ready"]
    updated["auto_approve_allowed"] = sto["auto_approve_allowed"]
    updated["agent_recommendation"] = sto["ud_recommendation"]
    updated["validation_checks"] = _build_validation_checks(
        document,
        participants_count=participants_count,
    )
    updated["warnings"] = _build_warnings(updated["validation_checks"])
    if isinstance(updated.get("queue"), dict):
        updated["queue"] = {**updated["queue"], "warnings": updated["warnings"]}
    if include_series_planning:
        refresh_series_planning(updated)
    return updated


async def _persist_memo_detail_cache(ref_key: str, detail: dict[str, Any]) -> None:
    """Сохраняет detail (с текстом СЗ) в Redis, не затирая fetched_at при обновлении."""
    if not settings.MEETING_DASHBOARD_CACHE_ENABLED:
        return
    service = MeetingMemoCacheService()
    normalized = ref_key.strip().lower()
    cached = await service._read_cache(normalized)
    if cached is not None:
        base = dict(cached["payload"])
        app = dict(base.get("application") or {})
        queue = dict(base.get("queue") or {})
        src_app = detail.get("application") or {}
        src_queue = detail.get("queue") or {}
        if src_app.get("memo_text"):
            app["memo_text"] = src_app.get("memo_text")
        if src_queue.get("ТекстСлужебнойЗаписки"):
            queue["ТекстСлужебнойЗаписки"] = src_queue.get("ТекстСлужебнойЗаписки")
        if src_app.get("memo_text_unavailable"):
            app["memo_text_unavailable"] = True
        if src_queue.get("memo_text_unavailable"):
            queue["memo_text_unavailable"] = True
        base["application"] = app
        base["queue"] = queue
        await service._write_cache(normalized, base, fetched_at=cached["fetched_at"])
        return
    await service._write_cache(
        normalized,
        detail,
        fetched_at=datetime.now(timezone.utc),
    )


async def ensure_memo_text_in_detail(
    detail: dict[str, Any],
    *,
    ref_key: str | None = None,
) -> dict[str, Any]:
    """Подгружает ТекстСлужебнойЗаписки из 1С, если в кэше его нет, и сохраняет обратно."""
    app = dict(detail.get("application") or {})
    queue = dict(detail.get("queue") or {})
    if app.get("memo_text_unavailable") or queue.get("memo_text_unavailable"):
        return detail

    memo_text = extract_memo_text(queue=queue, application=app)
    if memo_text:
        app["memo_text"] = memo_text
        queue["ТекстСлужебнойЗаписки"] = memo_text
        detail["application"] = app
        detail["queue"] = queue
        return detail

    normalized_ref = (
        ref_key
        or detail.get("ref_key")
        or clean_text(queue.get("ref_key"))
        or clean_text(queue.get("Ref_Key"))
        or ""
    ).strip()
    if not normalized_ref:
        return detail

    try:
        header = await asyncio.to_thread(
            fetch_document_header,
            create_session(CONFIG),
            CONFIG,
            normalized_ref,
        )
    except Exception as exc:
        logger.warning("meeting_memo_text_fetch_failed: %s", exc)
        app["memo_text_unavailable"] = True
        queue["memo_text_unavailable"] = True
        detail["application"] = app
        detail["queue"] = queue
        await _persist_memo_detail_cache(normalized_ref, detail)
        return detail

    memo_text = extract_memo_text(header)
    if not memo_text:
        app["memo_text_unavailable"] = True
        queue["memo_text_unavailable"] = True
        detail["application"] = app
        detail["queue"] = queue
        await _persist_memo_detail_cache(normalized_ref, detail)
        return detail

    app["memo_text"] = memo_text
    queue["ТекстСлужебнойЗаписки"] = memo_text
    app.pop("memo_text_unavailable", None)
    queue.pop("memo_text_unavailable", None)
    detail["application"] = app
    detail["queue"] = queue
    await _persist_memo_detail_cache(normalized_ref, detail)
    return detail


async def enrich_memo_detail_payload(
    payload: dict[str, Any],
    *,
    ref_key: str,
    selected_mode: str | None = None,
) -> dict[str, Any]:
    updated = await ensure_memo_text_in_detail(payload, ref_key=ref_key)
    updated = refresh_cached_detail_assessment(updated, include_series_planning=False)
    await refresh_series_planning_async(
        updated,
        ref_key=ref_key,
        selected_mode=selected_mode,
    )
    return updated


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
    async def get_series_planning_choice(self, ref_key: str) -> str | None:
        normalized = ref_key.strip().lower()
        try:
            raw = await meeting_redis_get(_series_mode_cache_key(normalized))
        except Exception as exc:
            logger.warning(
                "meeting_memo_series_mode_read_failed",
                ref_key=normalized,
                error=str(exc),
            )
            return None
        if raw in {"series", "single"}:
            return raw
        return None

    async def set_series_planning_choice(self, ref_key: str, mode: str) -> None:
        normalized = ref_key.strip().lower()
        if mode not in {"series", "single"}:
            raise ValueError("mode must be 'series' or 'single'")
        await meeting_redis_setex(
            _series_mode_cache_key(normalized),
            settings.MEETING_DASHBOARD_CACHE_TTL_SECONDS,
            mode,
        )

    async def _apply_cached_series_choice(
        self,
        payload: dict[str, Any],
        ref_key: str,
    ) -> dict[str, Any]:
        mode = await self.get_series_planning_choice(ref_key)
        if mode:
            await refresh_series_planning_async(payload, ref_key=ref_key, selected_mode=mode)
        return payload

    async def get_memo_detail(
        self,
        ref_key: str,
        *,
        target_date: date | None = None,
        force_refresh: bool = False,
    ) -> tuple[dict[str, Any], datetime, bool]:
        """Детали СЗ: memo-кэш, fallback из dashboard или загрузка из 1С (force_refresh)."""
        normalized = ref_key.strip().lower()
        if force_refresh:
            payload, fetched_at = await self._fetch_and_store(normalized)
            payload = await enrich_memo_detail_payload(payload, ref_key=normalized)
            payload = await self._apply_cached_series_choice(payload, normalized)
            return payload, fetched_at, False

        if not settings.MEETING_DASHBOARD_CACHE_ENABLED:
            raise MemoCacheMissError(
                "Кэш СЗ отключён. Включите MEETING_DASHBOARD_CACHE_ENABLED или обновите dashboard."
            )

        cached = await self._read_cache(normalized)
        if cached is not None:
            payload = await enrich_memo_detail_payload(
                cached["payload"],
                ref_key=normalized,
            )
            payload = await self._apply_cached_series_choice(payload, normalized)
            return payload, cached["fetched_at"], True

        fallback = await self._read_detail_from_dashboard_cache(
            normalized,
            target_date=target_date,
        )
        if fallback is not None:
            payload, fetched_at = fallback
            payload = await enrich_memo_detail_payload(payload, ref_key=normalized)
            payload = await self._apply_cached_series_choice(payload, normalized)
            return payload, fetched_at, True

        raise MemoCacheMissError(
            "Детали СЗ отсутствуют в кэше. Обновите dashboard."
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
            payload = await enrich_memo_detail_payload(
                cached["payload"],
                ref_key=normalized,
            )
            payload = await self._apply_cached_series_choice(payload, normalized)
            return payload, cached["fetched_at"], True

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

        payload = await enrich_memo_detail_payload(payload, ref_key=normalized)
        payload = await self._apply_cached_series_choice(payload, normalized)
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

    async def patch_meeting_slot(
        self,
        ref_key: str,
        *,
        slot_start: str,
        slot_end: str,
        location: str | None = None,
        history_message: str | None = None,
    ) -> bool:
        if not settings.MEETING_DASHBOARD_CACHE_ENABLED:
            return False
        normalized = ref_key.strip().lower()
        cached = await self._read_cache(normalized)
        if cached is None:
            return False
        patched = patch_detail_schedule(
            cached["payload"],
            slot_start=slot_start,
            slot_end=slot_end,
            location=location,
        )
        if history_message:
            from app.services.meeting_offline_cache import append_detail_history

            patched = append_detail_history(patched, history_message)
        await self._write_cache(normalized, patched, fetched_at=cached["fetched_at"])
        return True


async def warm_memo_details_from_dashboard(payload: dict[str, Any]) -> None:
    """Прогревает per-memo кэш текстом СЗ из dashboard (после refresh/warmup)."""
    if not settings.MEETING_DASHBOARD_CACHE_ENABLED:
        return

    service = MeetingMemoCacheService()
    fetched_at = datetime.now(timezone.utc)
    warmed = 0
    for item in _dashboard_items(payload):
        if not isinstance(item, dict):
            continue
        ref_key = (item.get("ref_key") or "").strip().lower()
        if not ref_key:
            continue
        memo_text = extract_memo_text(item)
        if not memo_text:
            continue

        cached = await service._read_cache(ref_key)
        if cached is not None:
            existing = cached["payload"]
            if extract_memo_text(
                queue=existing.get("queue") if isinstance(existing.get("queue"), dict) else None,
                application=(
                    existing.get("application")
                    if isinstance(existing.get("application"), dict)
                    else None
                ),
            ):
                continue
            app = dict(existing.get("application") or {})
            queue = dict(existing.get("queue") or {})
            app["memo_text"] = memo_text
            queue["ТекстСлужебнойЗаписки"] = memo_text
            app.pop("memo_text_unavailable", None)
            queue.pop("memo_text_unavailable", None)
            existing["application"] = app
            existing["queue"] = queue
            await service._write_cache(ref_key, existing, fetched_at=cached["fetched_at"])
        else:
            detail = build_detail_from_dashboard_item(item)
            await service._write_cache(ref_key, detail, fetched_at=fetched_at)
        warmed += 1

    if warmed:
        logger.info("meeting_memo_text_cache_warmed", count=warmed)
