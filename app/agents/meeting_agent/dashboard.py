from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote

import requests
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.meeting_agent.memo_presenter import build_queue_item_from_row
from app.core.logging import get_logger
from app.models.user import User
from app.schemas.meeting import MeetingDashboardItem, MeetingLoginContext
from app.services.meeting_permission import can_access_meeting_agent
from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.get_meetings import (
    DOCUMENT_ENTITY,
    build_meeting_theme_text_filter,
    entity_url,
    fetch_meeting_memo_rows,
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
    return f"{build_meeting_theme_base_filter()} and Статус eq '{UNAPPROVED_STATUS}'"


def build_today_meetings_filter(target_date: date) -> str:
    """СЗ на дату: дата документа (Date) = target_date, любой Статус согласования."""
    day = target_date.isoformat()
    base = build_meeting_theme_base_filter()
    return (
        f"({base}) and "
        f"(Date ge datetime'{day}T00:00:00' and Date lt datetime'{day}T23:59:59')"
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


def is_memo_document_date_on_date(row: dict[str, Any], target_date: date) -> bool:
    return parse_odata_datetime(row.get(MEMO_DOCUMENT_DATE_FIELD)) == target_date


def _clean_odata_value(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized.startswith(EMPTY_DATE):
        return None
    return normalized


def summarize_meeting_memo(row: dict[str, Any]) -> dict[str, Any]:
    return build_queue_item_from_row(row)


def _fetch_rows(
    session: requests.Session,
    config: ODataConfig,
    extra_filter: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    fetch_pool = max(limit, 1)
    try:
        return fetch_meeting_memo_rows(
            session,
            config,
            extra_filter,
            limit=limit,
            fetch_pool=fetch_pool,
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
    """Возвращает несогласованные СЗ и совещания на указанную дату из 1С OData."""
    day = target_date or date.today()
    session = create_session(config)

    unapproved_rows = _fetch_rows(
        session,
        config,
        f"Статус eq '{UNAPPROVED_STATUS}'",
        limit=limit,
    )
    today_rows = _fetch_rows(
        session,
        config,
        (
            f"Date ge datetime'{day.isoformat()}T00:00:00' "
            f"and Date lt datetime'{day.isoformat()}T23:59:59'"
        ),
        limit=limit,
    )

    unapproved = [summarize_meeting_memo(row) for row in unapproved_rows]
    today = [summarize_meeting_memo(row) for row in today_rows if is_memo_document_date_on_date(row, day)]

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
