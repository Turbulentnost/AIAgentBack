from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote

import requests
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.meeting_agent.memo_presenter import (
    ROOM_CATALOG,
    _header_with_people_keys,
    _load_users_by_keys,
    build_queue_item_from_row,
    collect_location_keys,
    load_catalog_descriptions,
)
from app.core.logging import get_logger
from app.models.user import User
from app.schemas.meeting import MeetingDashboardItem, MeetingLoginContext
from app.services.meeting_dashboard_cache import MeetingDashboardCacheService
from app.services.meeting_permission import can_access_meeting_agent
from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.lookup_user_ref import is_empty_key
from app.tools.onec.get_meetings import (
    DOCUMENT_ENTITY,
    build_meeting_theme_text_filter,
    entity_url,
    fetch_document_header,
    fetch_meeting_memo_rows,
    load_metadata_xml,
    meeting_theme,
    odata_get_json,
)

logger = get_logger(__name__)

EMPTY_DATE = "0001-01-01T00:00:00"
UNAPPROVED_STATUS = "НеСогласована"
MEMO_DOCUMENT_DATE_FIELD = "Date"
DEFAULT_DASHBOARD_LIMIT = 500


def build_meeting_theme_base_filter() -> str:
    if not meeting_theme():
        raise ValueError("ONEC_MEETING_MEMO_THEME is not configured")
    return build_meeting_theme_text_filter()


def build_unapproved_meetings_filter() -> str:
    return f"Статус eq '{UNAPPROVED_STATUS}'"


def build_today_meetings_filter(target_date: date) -> str:
    """СЗ за день по дате документа (Date), любой статус согласования."""
    day = target_date.isoformat()
    return (
        f"Date ge datetime'{day}T00:00:00' "
        f"and Date lt datetime'{day}T23:59:59'"
    )


from app.services.meeting_memo_document import parse_odata_date as parse_odata_datetime
from app.services.meeting_memo_document import clean_text, format_document_date_label


def normalize_dashboard_item(item: dict[str, Any]) -> dict[str, Any]:
    """Дополняет карточку очереди датой СЗ в формате для UI."""
    normalized = dict(item)
    raw_date = clean_text(normalized.get("Date")) or clean_text(normalized.get("document_date"))
    if not raw_date:
        return normalized
    label = format_document_date_label(raw_date)
    if label:
        normalized["document_date"] = label
        normalized["document_date_label"] = label
    return normalized


def normalize_dashboard_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "unapproved": [normalize_dashboard_item(item) for item in payload.get("unapproved") or []],
        "today": [normalize_dashboard_item(item) for item in payload.get("today") or []],
        "items": [normalize_dashboard_item(item) for item in payload.get("items") or []],
    }


def _dashboard_item_has_document_date(item: dict[str, Any]) -> bool:
    return bool(clean_text(item.get("Date")) or clean_text(item.get("document_date")))


def enrich_dashboard_payload_missing_dates(payload: dict[str, Any]) -> dict[str, Any]:
    """Нормализует карточки очереди без запросов в 1С (данные только из Redis)."""
    return normalize_dashboard_payload(payload)


def is_memo_document_date_on_date(row: dict[str, Any], target_date: date) -> bool:
    return parse_odata_datetime(row.get(MEMO_DOCUMENT_DATE_FIELD)) == target_date


def merge_dashboard_items(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def _clean_odata_value(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized.startswith(EMPTY_DATE):
        return None
    return normalized


def summarize_meeting_memo(
    row: dict[str, Any],
    *,
    location_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    return build_queue_item_from_row(row, location_labels=location_labels)


def _fetch_rows(
    session: requests.Session,
    config: ODataConfig,
    extra_filter: str,
    *,
    limit: int,
    metadata,
) -> list[dict[str, Any]]:
    fetch_pool = max(limit, 1)
    try:
        return fetch_meeting_memo_rows(
            session,
            config,
            extra_filter,
            limit=limit,
            fetch_pool=fetch_pool,
            metadata=metadata,
        )
    except RuntimeError:
        odata_filter = f"{build_meeting_theme_base_filter()} and ({extra_filter})"
        order = quote("Date desc, Number desc", safe=", ")
        url = (
            f"{entity_url(config.url, DOCUMENT_ENTITY)}"
            f"?$filter={quote(odata_filter, safe='')}"
            f"&$orderby={order}"
            f"&$top={fetch_pool}&$format=json"
        )
        data = odata_get_json(session, url, timeout=config.timeout)
        return data.get("value") or []


def get_meeting_dashboard(
    *,
    target_date: date | None = None,
    limit: int = DEFAULT_DASHBOARD_LIMIT,
    config: ODataConfig = CONFIG,
) -> dict[str, Any]:
    """Возвращает несогласованные СЗ за всё время и СЗ с датой документа за указанный день (любой статус)."""
    day = target_date or date.today()
    session = create_session(config)
    metadata = load_metadata_xml(session, config)

    unapproved_rows = _fetch_rows(
        session,
        config,
        f"Статус eq '{UNAPPROVED_STATUS}'",
        limit=limit,
        metadata=metadata,
    )
    today_rows = _fetch_rows(
        session,
        config,
        build_today_meetings_filter(day),
        limit=limit,
        metadata=metadata,
    )

    location_labels = load_catalog_descriptions(
        session,
        config,
        ROOM_CATALOG,
        collect_location_keys(unapproved_rows + today_rows),
    )

    source_by_ref: dict[str, dict[str, Any]] = {}
    for row in unapproved_rows + today_rows:
        ref_key = row.get("Ref_Key")
        if ref_key:
            source_by_ref[ref_key] = row

    enriched_by_ref = {
        ref_key: _header_with_people_keys(row, session=session, config=config)
        for ref_key, row in source_by_ref.items()
    }
    people_keys = {
        key
        for header in enriched_by_ref.values()
        for key in (header.get("Ответственный_Key"), header.get("РуководительСовещания_Key"))
        if key and not is_empty_key(key)
    }
    users_by_key = (
        _load_users_by_keys(session, config, list(people_keys))
        if people_keys
        else {}
    )

    unapproved = [
        build_queue_item_from_row(
            enriched_by_ref.get(row["Ref_Key"], row),
            location_labels=location_labels,
            session=session,
            config=config,
            users_by_key=users_by_key,
        )
        for row in unapproved_rows
    ]
    today = [
        build_queue_item_from_row(
            enriched_by_ref.get(row["Ref_Key"], row),
            location_labels=location_labels,
            session=session,
            config=config,
            users_by_key=users_by_key,
        )
        for row in today_rows
        if is_memo_document_date_on_date(row, day)
    ]
    items = merge_dashboard_items(unapproved, today)

    return {
        "date": day.isoformat(),
        "unapproved": unapproved,
        "today": today,
        "items": items,
        "counts": {
            "unapproved": len(unapproved),
            "today": len(today),
            "items": len(items),
        },
    }


def _build_login_context(
    payload: dict[str, Any],
    *,
    fetched_at: datetime,
    error: str | None = None,
    fallback_date: str | None = None,
) -> MeetingLoginContext:
    payload = normalize_dashboard_payload(payload)
    items_raw = payload.get("items")
    if not items_raw:
        items_raw = merge_dashboard_items(
            payload.get("unapproved") or [],
            payload.get("today") or [],
        )
    day_label = str(payload.get("date") or fallback_date or date.today().isoformat())
    return MeetingLoginContext(
        date=day_label,
        unapproved=[MeetingDashboardItem.model_validate(item) for item in payload["unapproved"]],
        today=[MeetingDashboardItem.model_validate(item) for item in payload["today"]],
        items=[MeetingDashboardItem.model_validate(item) for item in items_raw],
        counts=payload.get("counts") or {},
        fetched_at=fetched_at,
        error=error,
    )


async def load_login_context(
    db: AsyncSession,
    user: User,
    *,
    target_date: date | None = None,
    force_refresh: bool = False,
) -> MeetingLoginContext | None:
    if not await can_access_meeting_agent(db, user):
        return None

    day = target_date or date.today()
    cache = MeetingDashboardCacheService()
    fetch_error: str | None = None
    try:
        if force_refresh:
            payload, fetched_at, _from_cache, fetch_error = await cache.refresh_dashboard(target_date=day)
        else:
            payload, fetched_at, from_cache = await cache.get_dashboard(target_date=day)
            if from_cache:
                payload = await asyncio.to_thread(enrich_dashboard_payload_missing_dates, payload)
    except Exception as exc:
        logger.warning("meeting_login_context_failed", user_id=str(user.id), error=str(exc))
        return MeetingLoginContext(
            date=day.isoformat(),
            fetched_at=datetime.now(timezone.utc),
            error=str(exc),
        )

    try:
        return _build_login_context(
            payload,
            fetched_at=fetched_at,
            error=fetch_error,
            fallback_date=day.isoformat(),
        )
    except Exception as exc:
        logger.warning(
            "meeting_login_context_build_failed",
            user_id=str(user.id),
            date=day.isoformat(),
            error=str(exc),
        )
        return MeetingLoginContext(
            date=day.isoformat(),
            fetched_at=fetched_at,
            error=str(exc) or fetch_error,
        )
