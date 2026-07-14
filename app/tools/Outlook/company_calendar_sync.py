"""Зеркалирование совещаний Postagent в общий календарь компании."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from exchangelib import CalendarItem
from exchangelib.errors import ErrorItemNotFound
from exchangelib.items import SEND_TO_NONE

from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.send_meeting_invite import load_config, primary_smtp_address

logger = logging.getLogger(__name__)


def company_calendar_address(config: OutlookConfig | None = None) -> str | None:
    resolved = config or load_config()
    email = (resolved.company_calendar or "").strip()
    if not email:
        return None
    if email.lower() == primary_smtp_address(resolved).lower():
        return None
    return email


def is_company_calendar_email(email: str, *, config: OutlookConfig | None = None) -> bool:
    address = company_calendar_address(config)
    if not address:
        return False
    return email.strip().lower() == address.lower()


def company_calendar_sync_meta(
    *,
    synced: bool,
    config: OutlookConfig | None = None,
    item: Any | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    address = company_calendar_address(config)
    meta: dict[str, Any] = {
        "company_calendar_synced": synced,
        "company_calendar": address,
    }
    if item is not None:
        meta["company_calendar_item_id"] = getattr(item, "id", None)
        meta["company_calendar_changekey"] = getattr(item, "changekey", None)
    if error:
        meta["company_calendar_error"] = error
    return meta


def _copy_fields_from_source(item: CalendarItem, source: Any) -> None:
    item.subject = getattr(source, "subject", None) or ""
    item.body = getattr(source, "body", None)
    item.start = getattr(source, "start", None)
    item.end = getattr(source, "end", None)
    item.location = getattr(source, "location", None) or ""
    item.required_attendees = list(getattr(source, "required_attendees", None) or [])
    item.optional_attendees = list(getattr(source, "optional_attendees", None) or [])
    resources = getattr(source, "resources", None)
    if resources is not None:
        item.resources = list(resources)
    recurrence = getattr(source, "recurrence", None)
    if recurrence is not None:
        item.recurrence = recurrence


def get_company_calendar_item(
    config: OutlookConfig,
    *,
    item_id: str,
    changekey: str = "",
) -> Any | None:
    from app.tools.Outlook.read_calendars import connect_as_owner

    company_email = company_calendar_address(config)
    if not company_email or not item_id.strip():
        return None
    account = connect_as_owner(config, company_email)
    try:
        fetched = list(
            account.fetch(
                ids=[(item_id.strip(), (changekey or "").strip() or None)],
            )
        )
    except ErrorItemNotFound:
        return None
    except Exception:
        return None
    if not fetched or isinstance(fetched[0], ErrorItemNotFound):
        return None
    return fetched[0]


def find_company_calendar_item(
    config: OutlookConfig,
    *,
    subject: str,
    start: Any,
    tolerance_minutes: int = 5,
) -> Any | None:
    from app.tools.Outlook.cancel_meeting import to_local
    from app.tools.Outlook.read_calendars import read_calendar_items_in_range

    company_email = company_calendar_address(config)
    if not company_email or start is None:
        return None
    start_local = to_local(start, config)
    window_start = start_local - timedelta(minutes=max(tolerance_minutes, 0))
    window_end = start_local + timedelta(minutes=max(tolerance_minutes, 0))
    try:
        items = read_calendar_items_in_range(
            config,
            company_email,
            range_start=window_start,
            range_end=window_end,
            max_items=50,
        )
    except Exception:
        return None

    subject_norm = subject.strip().lower()
    matches: list[Any] = []
    for item in items:
        if getattr(item, "is_cancelled", False):
            continue
        item_subject = (getattr(item, "subject", None) or "").strip().lower()
        if subject_norm and subject_norm not in item_subject:
            continue
        if not getattr(item, "start", None):
            continue
        delta = abs((to_local(item.start, config) - start_local).total_seconds())
        if delta <= max(tolerance_minutes, 0) * 60:
            matches.append((delta, item))
    if not matches:
        return None
    matches.sort(key=lambda pair: pair[0])
    return matches[0][1]


def sync_meeting_to_company_calendar(
    source_item: Any,
    *,
    config: OutlookConfig | None = None,
    company_item_id: str | None = None,
    company_changekey: str | None = None,
) -> dict[str, Any]:
    """Создаёт или обновляет копию совещания в общем календаре без рассылки приглашений."""
    resolved = config or load_config()
    company_email = company_calendar_address(resolved)
    if not company_email:
        return company_calendar_sync_meta(synced=False, config=resolved)

    try:
        from app.tools.Outlook.read_calendars import connect_as_owner

        account = connect_as_owner(resolved, company_email)
        existing = None
        if company_item_id:
            existing = get_company_calendar_item(
                resolved,
                item_id=company_item_id,
                changekey=company_changekey or "",
            )
        if existing is None:
            existing = find_company_calendar_item(
                resolved,
                subject=str(getattr(source_item, "subject", "") or ""),
                start=getattr(source_item, "start", None),
            )

        if existing is not None:
            _copy_fields_from_source(existing, source_item)
            existing.save(send_meeting_invitations=SEND_TO_NONE)
            return company_calendar_sync_meta(synced=True, config=resolved, item=existing)

        item = CalendarItem(account=account, folder=account.calendar)
        _copy_fields_from_source(item, source_item)
        item.save(send_meeting_invitations=SEND_TO_NONE)
        return company_calendar_sync_meta(synced=True, config=resolved, item=item)
    except Exception as error:
        logger.warning(
            "company_calendar_sync_failed calendar=%s error=%s",
            company_email,
            error,
        )
        return company_calendar_sync_meta(
            synced=False,
            config=resolved,
            error=str(error),
        )


def cancel_meeting_in_company_calendar(
    *,
    config: OutlookConfig | None = None,
    company_item_id: str | None = None,
    company_changekey: str | None = None,
    subject: str = "",
    start: Any = None,
) -> dict[str, Any]:
    resolved = config or load_config()
    company_email = company_calendar_address(resolved)
    if not company_email:
        return company_calendar_sync_meta(synced=False, config=resolved)

    try:
        item = None
        if company_item_id:
            item = get_company_calendar_item(
                resolved,
                item_id=company_item_id,
                changekey=company_changekey or "",
            )
        if item is None and start is not None:
            item = find_company_calendar_item(
                resolved,
                subject=subject,
                start=start,
            )
        if item is None:
            return company_calendar_sync_meta(synced=False, config=resolved)

        if getattr(item, "is_cancelled", False):
            return company_calendar_sync_meta(synced=True, config=resolved, item=item)

        delete = getattr(item, "delete", None)
        if callable(delete):
            delete()
        else:
            item.cancel()
        return company_calendar_sync_meta(synced=True, config=resolved, item=item)
    except Exception as error:
        logger.warning(
            "company_calendar_cancel_failed calendar=%s error=%s",
            company_email,
            error,
        )
        return company_calendar_sync_meta(
            synced=False,
            config=resolved,
            error=str(error),
        )


__all__ = [
    "cancel_meeting_in_company_calendar",
    "company_calendar_address",
    "company_calendar_sync_meta",
    "find_company_calendar_item",
    "get_company_calendar_item",
    "is_company_calendar_email",
    "sync_meeting_to_company_calendar",
]
