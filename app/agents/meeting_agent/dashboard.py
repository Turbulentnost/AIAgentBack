from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote

import requests
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.meeting_agent.schemas import MeetingDashboardItem, MeetingLoginContext
from app.core.logging import get_logger
from app.models.user import User
from app.services.meeting_permission import can_access_meeting_agent
from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.get_meetings import (
    DOCUMENT_ENTITY,
    entity_url,
    fetch_documents_by_filter,
    meeting_theme,
    odata_get_json,
)

logger = get_logger(__name__)

EMPTY_DATE = "0001-01-01T00:00:00"
UNAPPROVED_STATUS = "НеСогласована"
MEETING_DATE_FIELDS = (
    "ДатаПроведенияСовещания",
    "ЖелаемаяДатаПроведенияСовещания",
    "ВремяНачалаСовещания",
)
DEFAULT_DASHBOARD_LIMIT = 500


def build_meeting_theme_base_filter() -> str:
    theme = meeting_theme().replace("'", "''")
    if not theme:
        raise ValueError("ONEC_MEETING_MEMO_THEME is not configured")
    return f"(ТемаСлужебнойЗаписки eq '{theme}') and DeletionMark eq false and Posted eq true"


def build_unapproved_meetings_filter() -> str:
    return f"{build_meeting_theme_base_filter()} and Статус eq '{UNAPPROVED_STATUS}'"


def build_today_meetings_filter(target_date: date) -> str:
    day = target_date.isoformat()
    base = build_meeting_theme_base_filter()
    return (
        f"({base}) and ("
        f"(ДатаПроведенияСовещания ge datetime'{day}T00:00:00' "
        f"and ДатаПроведенияСовещания lt datetime'{day}T23:59:59')"
        f" or (ЖелаемаяДатаПроведенияСовещания ge datetime'{day}T00:00:00' "
        f"and ЖелаемаяДатаПроведенияСовещания lt datetime'{day}T23:59:59')"
        f" or (ВремяНачалаСовещания ge datetime'{day}T00:00:00' "
        f"and ВремяНачалаСовещания lt datetime'{day}T23:59:59')"
        f")"
    )


def parse_odata_datetime(value: str | None) -> date | None:
    if not value or not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized.startswith(EMPTY_DATE):
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def is_meeting_on_date(row: dict[str, Any], target_date: date) -> bool:
    for field in MEETING_DATE_FIELDS:
        if parse_odata_datetime(row.get(field)) == target_date:
            return True
    return False


def _clean_odata_value(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized.startswith(EMPTY_DATE):
        return None
    return normalized


def summarize_meeting_memo(row: dict[str, Any]) -> dict[str, Any]:
    subject = (row.get("ТемаСовещания") or "").strip()
    comment = (row.get("Комментарий") or "").strip()
    return {
        "ref_key": row.get("Ref_Key"),
        "number": row.get("Number"),
        "document_date": _clean_odata_value(row.get("Date")),
        "status": row.get("Статус"),
        "meeting_date": _clean_odata_value(row.get("ДатаПроведенияСовещания")),
        "desired_meeting_date": _clean_odata_value(row.get("ЖелаемаяДатаПроведенияСовещания")),
        "meeting_start": _clean_odata_value(row.get("ВремяНачалаСовещания")),
        "meeting_end": _clean_odata_value(row.get("ВремяОкончанияСовещания")),
        "subject": subject or comment or None,
        "meeting_type": row.get("ВидСовещания") or None,
        "comment": comment or None,
        "location": row.get("МестоПроведенияСовещания") or None,
    }


def _fetch_rows(
    session: requests.Session,
    config: ODataConfig,
    odata_filter: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    fetch_pool = max(limit, 1)
    try:
        return fetch_documents_by_filter(
            session,
            config,
            odata_filter,
            limit=limit,
            fetch_pool=fetch_pool,
        )
    except RuntimeError:
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
    """Возвращает несогласованные СЗ и совещания на указанную дату из 1С OData."""
    day = target_date or date.today()
    session = create_session(config)

    unapproved_rows = _fetch_rows(
        session,
        config,
        build_unapproved_meetings_filter(),
        limit=limit,
    )
    today_rows = _fetch_rows(
        session,
        config,
        build_today_meetings_filter(day),
        limit=limit,
    )

    unapproved = [summarize_meeting_memo(row) for row in unapproved_rows]
    today = [summarize_meeting_memo(row) for row in today_rows if is_meeting_on_date(row, day)]

    return {
        "date": day.isoformat(),
        "unapproved": unapproved,
        "today": today,
        "counts": {
            "unapproved": len(unapproved),
            "today": len(today),
        },
    }


def _build_login_context(payload: dict[str, Any], *, fetched_at: datetime) -> MeetingLoginContext:
    return MeetingLoginContext(
        date=payload["date"],
        unapproved=[MeetingDashboardItem.model_validate(item) for item in payload["unapproved"]],
        today=[MeetingDashboardItem.model_validate(item) for item in payload["today"]],
        counts=payload.get("counts") or {},
        fetched_at=fetched_at,
    )


async def load_login_context(
    db: AsyncSession,
    user: User,
    *,
    target_date: date | None = None,
) -> MeetingLoginContext | None:
    if not await can_access_meeting_agent(db, user):
        return None

    fetched_at = datetime.now(timezone.utc)
    day = target_date or date.today()
    try:
        payload = await asyncio.to_thread(get_meeting_dashboard, target_date=day)
    except Exception as exc:
        logger.warning("meeting_login_context_failed", user_id=str(user.id), error=str(exc))
        return MeetingLoginContext(
            date=day.isoformat(),
            fetched_at=fetched_at,
            error=str(exc),
        )

    return _build_login_context(payload, fetched_at=fetched_at)
