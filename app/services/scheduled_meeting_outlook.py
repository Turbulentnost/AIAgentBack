from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from exchangelib.items import SEND_ONLY_TO_ALL
from exchangelib.recurrence import (
    AbsoluteMonthlyPattern,
    DailyPattern,
    Recurrence,
    RelativeMonthlyPattern,
    WeeklyPattern,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    ScheduledMeetingFrequency,
    ScheduledMeetingMonthlyMode,
    ScheduledMeetingStatus,
    ScheduledMeetingWeekday,
    ScheduledMeetingWeekdayPosition,
)
from app.models.scheduled_meeting import ScheduledMeeting
from app.models.position import Position
from app.models.user import User
from app.services.enterprise_positions_report import (
    lookup_fios_by_position_title,
    normalize_position_title,
)
from app.services.meeting_invite_format import INVITE_AGENT_FOOTER, format_invite_body
from app.tools.Outlook.outlook_html_body import plain_text_to_html
from app.tools.Outlook.outlook_meeting_link import calendar_item_outlook_meta
from app.tools.Outlook.send_meeting_invite import (
    connect_account,
    load_config,
    parse_start,
    resolve_attendee,
)
from app.tools.Outlook.send_recurring_meeting_invite import (
    WEEKDAY_NAMES,
    dispatch_recurring_meeting_invite,
    weekday_mismatch_warning,
)
from app.utils.department_classification import normalize_position_name

logger = logging.getLogger(__name__)


class ScheduledMeetingOutlookError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


_WEEKDAY_TO_OUTLOOK: dict[ScheduledMeetingWeekday, str] = {
    ScheduledMeetingWeekday.MONDAY: "Monday",
    ScheduledMeetingWeekday.TUESDAY: "Tuesday",
    ScheduledMeetingWeekday.WEDNESDAY: "Wednesday",
    ScheduledMeetingWeekday.THURSDAY: "Thursday",
    ScheduledMeetingWeekday.FRIDAY: "Friday",
    ScheduledMeetingWeekday.SATURDAY: "Saturday",
    ScheduledMeetingWeekday.SUNDAY: "Sunday",
}

_WEEKDAY_POSITION_TO_OUTLOOK: dict[ScheduledMeetingWeekdayPosition, str] = {
    ScheduledMeetingWeekdayPosition.FIRST: "First",
    ScheduledMeetingWeekdayPosition.SECOND: "Second",
    ScheduledMeetingWeekdayPosition.THIRD: "Third",
    ScheduledMeetingWeekdayPosition.FOURTH: "Fourth",
    ScheduledMeetingWeekdayPosition.LAST: "Last",
}


def _combine_start(meeting: ScheduledMeeting, timezone: str) -> datetime:
    start_date = meeting.series_start_date.isoformat()
    time_label = meeting.time_local.strftime("%H:%M")
    return parse_start(f"{start_date} {time_label}", timezone)


def _invite_body(meeting: ScheduledMeeting, attendees: list[tuple[str, str]]) -> str:
    return format_invite_body(attendees, footer=INVITE_AGENT_FOOTER)


def _attendee_emails(attendees: list[tuple[str, str]]) -> list[str]:
    emails: list[str] = []
    seen: set[str] = set()
    for _fio, address in attendees:
        key = address.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        emails.append(address.strip())
    return emails


def _build_recurrence(meeting: ScheduledMeeting, start: datetime) -> Recurrence:
    interval = meeting.interval
    start_date = start.date()
    end_date = meeting.series_end_date

    if meeting.frequency == ScheduledMeetingFrequency.DAILY:
        pattern = DailyPattern(interval=interval)
    elif meeting.frequency == ScheduledMeetingFrequency.WEEKLY:
        if meeting.weekday is None:
            raise ScheduledMeetingOutlookError("Для weekly не задан weekday")
        pattern = WeeklyPattern(
            interval=interval,
            weekdays=[_WEEKDAY_TO_OUTLOOK[meeting.weekday]],
        )
    elif meeting.frequency == ScheduledMeetingFrequency.MONTHLY:
        if meeting.monthly_mode == ScheduledMeetingMonthlyMode.BY_DAY_OF_MONTH:
            if meeting.day_of_month is None:
                raise ScheduledMeetingOutlookError("Для monthly не задан day_of_month")
            pattern = AbsoluteMonthlyPattern(
                interval=interval,
                day_of_month=meeting.day_of_month,
            )
        elif meeting.monthly_mode == ScheduledMeetingMonthlyMode.BY_WEEKDAY_POSITION:
            if meeting.weekday is None or meeting.weekday_position is None:
                raise ScheduledMeetingOutlookError(
                    "Для monthly by_weekday_position не заданы weekday/weekday_position"
                )
            pattern = RelativeMonthlyPattern(
                interval=interval,
                weekday=WEEKDAY_NAMES.index(_WEEKDAY_TO_OUTLOOK[meeting.weekday]),
                week_number=list(_WEEKDAY_POSITION_TO_OUTLOOK.values()).index(
                    _WEEKDAY_POSITION_TO_OUTLOOK[meeting.weekday_position]
                ),
            )
        else:
            raise ScheduledMeetingOutlookError("Для monthly не задан monthly_mode")
    elif meeting.frequency == ScheduledMeetingFrequency.YEARLY:
        pattern = AbsoluteMonthlyPattern(interval=12, day_of_month=start_date.day)
    else:
        raise ScheduledMeetingOutlookError(f"Неизвестная частота: {meeting.frequency}")

    if end_date < start_date:
        raise ScheduledMeetingOutlookError("series_end_date не может быть раньше series_start_date")

    return Recurrence(pattern=pattern, start=start_date, end=end_date)


def _dispatch_relative_monthly_invite(
    *,
    meeting: ScheduledMeeting,
    attendees: list[str],
    start: datetime,
) -> dict[str, Any]:
    from exchangelib import CalendarItem

    from app.tools.Outlook.company_calendar_sync import sync_meeting_to_company_calendar
    from app.tools.Outlook.send_meeting_invite import load_config as _load_config
    from app.tools.Outlook.send_meeting_invite import primary_smtp_address

    config = _load_config()
    account = connect_account(config)
    meeting_end = start + timedelta(minutes=meeting.duration_minutes)
    recurrence = _build_recurrence(meeting, start)
    body = _invite_body(meeting, attendees)

    item = CalendarItem(
        account=account,
        folder=account.calendar,
        subject=meeting.title.strip(),
        body=plain_text_to_html(body),
        start=start,
        end=meeting_end,
        location="",
        recurrence=recurrence,
        required_attendees=[resolve_attendee(person) for person in _attendee_emails(attendees)],
        resources=[],
    )
    item.save(send_meeting_invitations=SEND_ONLY_TO_ALL)
    outlook_meta = calendar_item_outlook_meta(item, config)
    company_meta = sync_meeting_to_company_calendar(item, config=config)
    return {
        "status": "sent",
        "from": primary_smtp_address(config),
        "attendees": _attendee_emails(attendees),
        "subject": meeting.title.strip(),
        "start": start.isoformat(),
        "end": meeting_end.isoformat(),
        "duration_minutes": meeting.duration_minutes,
        "warning": None,
        **outlook_meta,
        **company_meta,
    }


def dispatch_scheduled_meeting_invite(
    meeting: ScheduledMeeting,
    *,
    attendees: list[tuple[str, str]],
) -> dict[str, Any]:
    config = load_config()
    start = _combine_start(meeting, config.timezone)
    attendee_emails = _attendee_emails(attendees)
    if not attendee_emails:
        raise ScheduledMeetingOutlookError("Не удалось определить e-mail участников серии")

    if (
        meeting.frequency == ScheduledMeetingFrequency.MONTHLY
        and meeting.monthly_mode == ScheduledMeetingMonthlyMode.BY_WEEKDAY_POSITION
    ):
        return _dispatch_relative_monthly_invite(
            meeting=meeting,
            attendees=attendees,
            start=start,
        )

    pattern: str
    weekdays: list[str] | None = None
    day_of_month: int | None = None

    if meeting.frequency == ScheduledMeetingFrequency.DAILY:
        pattern = "daily"
    elif meeting.frequency == ScheduledMeetingFrequency.WEEKLY:
        pattern = "weekly"
        if meeting.weekday is None:
            raise ScheduledMeetingOutlookError("Для weekly не задан weekday")
        weekdays = [_WEEKDAY_TO_OUTLOOK[meeting.weekday]]
    elif meeting.frequency == ScheduledMeetingFrequency.MONTHLY:
        pattern = "monthly"
        day_of_month = meeting.day_of_month
    elif meeting.frequency == ScheduledMeetingFrequency.YEARLY:
        pattern = "monthly"
        day_of_month = meeting.series_start_date.day
    else:
        raise ScheduledMeetingOutlookError(f"Неизвестная частота: {meeting.frequency}")

    interval = meeting.interval if meeting.frequency != ScheduledMeetingFrequency.YEARLY else 12
    warning = (
        weekday_mismatch_warning(start=start, weekdays=weekdays)
        if pattern == "weekly"
        else None
    )
    result = dispatch_recurring_meeting_invite(
        attendee=attendee_emails[0],
        attendees=attendee_emails,
        subject=meeting.title.strip(),
        start=start.strftime("%Y-%m-%d %H:%M"),
        duration_minutes=meeting.duration_minutes,
        pattern=pattern,  # type: ignore[arg-type]
        interval=interval,
        weekdays=weekdays,
        day_of_month=day_of_month,
        end_type="end_date",
        end=meeting.series_end_date.isoformat(),
        body=_invite_body(meeting, attendees),
        timezone=config.timezone,
        config=config,
    )
    if warning and not result.get("warning"):
        result["warning"] = warning
    return result


def _is_invitable_attendee_email(address: str) -> bool:
    from app.services.employee_sync_service import SYNC_EMAIL_DOMAIN
    from app.tools.onec.lookup_email_by_fio import is_corporate_email

    normalized = address.strip().lower()
    if not normalized:
        return False
    if normalized.endswith(f"@{SYNC_EMAIL_DOMAIN}"):
        return False
    return is_corporate_email(address)


async def _load_users_by_position(db: AsyncSession) -> dict[str, list[tuple[str, str]]]:
    users_result = await db.execute(
        select(User.full_name, User.email, User.position).where(
            User.deleted_at.is_(None),
            User.is_active.is_(True),
            User.email.is_not(None),
            User.position.is_not(None),
        )
    )
    users_by_position: dict[str, list[tuple[str, str]]] = {}
    for full_name, email, position in users_result.all():
        position_key = normalize_position_title(normalize_position_name(position or ""))
        if not position_key:
            continue
        fio = (full_name or "").strip()
        address = (email or "").strip()
        if not address or not _is_invitable_attendee_email(address):
            continue
        users_by_position.setdefault(position_key, []).append((fio, address))
    return users_by_position


async def resolve_attendees_for_position_titles(
    db: AsyncSession,
    titles: list[str],
) -> list[tuple[str, str]]:
    from app.tools.onec.lookup_email_by_fio import lookup_email_by_fio

    users_by_position = await _load_users_by_position(db)
    attendees: list[tuple[str, str]] = []
    seen: set[str] = set()
    unresolved: list[str] = []

    for title in titles:
        normalized_title = normalize_position_title(title)
        if not normalized_title:
            unresolved.append(title)
            continue

        found_for_role = False
        for fio, address in users_by_position.get(normalized_title, []):
            key = address.lower()
            if key in seen:
                found_for_role = True
                continue
            seen.add(key)
            attendees.append((fio or title, address))
            found_for_role = True

        for fio in lookup_fios_by_position_title(title):
            payload = await asyncio.to_thread(lookup_email_by_fio, fio)
            for entry in payload.get("emails") or []:
                address = (entry.get("email") or "").strip()
                if not address or not _is_invitable_attendee_email(address):
                    continue
                key = address.lower()
                if key in seen:
                    found_for_role = True
                    continue
                seen.add(key)
                attendees.append((fio.strip() or title, address))
                found_for_role = True

        if not found_for_role:
            unresolved.append(title)

    if unresolved:
        raise ScheduledMeetingOutlookError(
            "Не удалось найти e-mail для должностей: " + ", ".join(unresolved)
        )
    if not attendees:
        raise ScheduledMeetingOutlookError("Не удалось определить e-mail участников серии")
    return attendees


def _fio_matches_series_attendee_name(fio: str, display_name: str | None) -> bool:
    if not display_name:
        return False
    surname = fio.split()[0].casefold() if fio.split() else ""
    if surname and surname in display_name.casefold():
        return True
    parts = [part for part in fio.split() if len(part) > 1]
    return bool(parts) and all(part.casefold() in display_name.casefold() for part in parts[:2])


def _emails_for_removed_position_in_series(
    title: str,
    *,
    series_attendees: list[tuple[str | None, str]],
    users_by_position: dict[str, list[tuple[str, str]]],
) -> list[str]:
    series_email_set = {email.lower() for _name, email in series_attendees}
    normalized_title = normalize_position_title(normalize_position_name(title))
    matched: list[str] = []
    seen: set[str] = set()

    for _fio, address in users_by_position.get(normalized_title, []):
        key = address.strip().lower()
        if key in series_email_set and key not in seen:
            seen.add(key)
            matched.append(address.strip())

    if not matched:
        for fio in lookup_fios_by_position_title(title):
            for display_name, email in series_attendees:
                key = email.lower()
                if key in seen:
                    continue
                if _fio_matches_series_attendee_name(fio, display_name):
                    seen.add(key)
                    matched.append(email)

    return matched


def _load_outlook_series_item(meeting: ScheduledMeeting) -> Any:
    from app.tools.Outlook.cancel_meeting import get_meeting_by_id
    from app.tools.Outlook.slot_search.attendees import (
        calendar_item_attendee_emails,
        calendar_item_attendee_entries,
    )

    if not meeting.outlook_series_id:
        raise ScheduledMeetingOutlookError(
            "Серия не связана с календарём Outlook",
            status_code=409,
        )

    config = load_config()
    item = get_meeting_by_id(
        config=config,
        item_id=meeting.outlook_series_id,
        changekey=meeting.outlook_changekey or "",
    )
    if not calendar_item_attendee_emails(item):
        try:
            item.refresh()
        except Exception as exc:
            logger.warning("outlook_series_refresh_failed error=%s", exc)

    if not calendar_item_attendee_entries(item):
        raise ScheduledMeetingOutlookError(
            "В серии Outlook не найдены участники для удаления",
            status_code=409,
        )
    return item


def _resolve_removed_emails_from_outlook_series_sync(
    meeting: ScheduledMeeting,
    titles: list[str],
    users_by_position: dict[str, list[tuple[str, str]]],
) -> list[str]:
    from app.tools.Outlook.slot_search.attendees import calendar_item_attendee_entries

    item = _load_outlook_series_item(meeting)
    series_attendees = calendar_item_attendee_entries(item)
    emails: list[str] = []
    seen: set[str] = set()
    unresolved: list[str] = []

    for title in titles:
        role_emails = _emails_for_removed_position_in_series(
            title,
            series_attendees=series_attendees,
            users_by_position=users_by_position,
        )
        if not role_emails:
            unresolved.append(title)
            continue
        for address in role_emails:
            key = address.lower()
            if key in seen:
                continue
            seen.add(key)
            emails.append(address)

    if unresolved:
        raise ScheduledMeetingOutlookError(
            "Не удалось сопоставить удаляемые должности с участниками серии Outlook: "
            + ", ".join(unresolved)
        )
    if not emails:
        raise ScheduledMeetingOutlookError(
            "Не удалось определить e-mail удаляемых участников серии Outlook"
        )
    return emails


async def resolve_removed_emails_from_outlook_series(
    db: AsyncSession,
    meeting: ScheduledMeeting,
    position_ids: tuple[uuid.UUID, ...],
) -> list[str]:
    """E-mail удаляемых участников из текущего состава серии Outlook (без GAL)."""
    if not position_ids:
        return []

    result = await db.execute(select(Position).where(Position.id.in_(position_ids)))
    positions = list(result.scalars().all())
    titles_by_id = {
        position.id: position.name.strip()
        for position in positions
        if position.name and position.name.strip()
    }
    if len(titles_by_id) != len(position_ids):
        missing = [str(position_id) for position_id in position_ids if position_id not in titles_by_id]
        raise ScheduledMeetingOutlookError(
            f"Не найдены должности для участников: {', '.join(missing)}"
        )

    users_by_position = await _load_users_by_position(db)
    titles = [titles_by_id[position_id] for position_id in position_ids]
    return await asyncio.to_thread(
        _resolve_removed_emails_from_outlook_series_sync,
        meeting,
        titles,
        users_by_position,
    )


async def _resolve_participant_invitable_email(
    db: AsyncSession,
    participant: Any,
) -> tuple[str, str] | None:
    fio = (getattr(participant, "person_fio", None) or "").strip()
    email = (getattr(participant, "person_email", None) or "").strip()
    if email and _is_invitable_attendee_email(email):
        return fio or email, email

    user_id = getattr(participant, "user_id", None)
    if user_id is not None:
        user = await db.get(User, user_id)
        if user is not None:
            from app.services.scheduled_meeting_person import _invitable_email_for_user

            invitable = await _invitable_email_for_user(user)
            if invitable:
                resolved_fio = fio or (user.full_name or "").strip() or invitable
                return resolved_fio, invitable

    if fio:
        from app.services.scheduled_meeting_person import _resolve_gal_person

        gal_match = await _resolve_gal_person(fio)
        if gal_match is not None:
            gal_fio, gal_email = gal_match
            if _is_invitable_attendee_email(gal_email):
                return gal_fio, gal_email

    return None


async def resolve_attendees(
    db: AsyncSession,
    meeting: ScheduledMeeting,
) -> list[tuple[str, str]]:
    attendees: list[tuple[str, str]] = []
    seen: set[str] = set()
    unresolved: list[str] = []

    for participant in sorted(meeting.participants, key=lambda item: item.sort_order):
        resolved = await _resolve_participant_invitable_email(db, participant)
        if resolved is None:
            fio = (getattr(participant, "person_fio", None) or "").strip()
            unresolved.append(
                fio or str(getattr(participant, "user_id", None) or getattr(participant, "id", ""))
            )
            continue
        fio, email = resolved
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        attendees.append((fio or email, email))

    if unresolved:
        raise ScheduledMeetingOutlookError(
            "Не удалось определить e-mail участников серии: " + ", ".join(unresolved)
        )
    if not attendees:
        raise ScheduledMeetingOutlookError("Не удалось определить e-mail участников серии")
    return attendees


async def resolve_attendee_emails(db: AsyncSession, meeting: ScheduledMeeting) -> list[str]:
    return _attendee_emails(await resolve_attendees(db, meeting))


async def plan_scheduled_meeting_in_outlook(
    db: AsyncSession,
    meeting: ScheduledMeeting,
) -> dict[str, Any]:
    if meeting.outlook_series_id:
        raise ScheduledMeetingOutlookError(
            "Серия уже распланирована в Outlook",
            status_code=409,
        )
    if meeting.status == ScheduledMeetingStatus.ARCHIVE:
        raise ScheduledMeetingOutlookError("Нельзя распланировать архивную серию", status_code=409)

    attendees = await resolve_attendees(db, meeting)
    result = await asyncio.to_thread(
        dispatch_scheduled_meeting_invite,
        meeting,
        attendees=attendees,
    )

    meeting.outlook_series_id = result.get("outlook_item_id")
    meeting.outlook_changekey = result.get("outlook_changekey")
    meeting.outlook_meeting_url = result.get("outlook_meeting_url")
    meeting.status = ScheduledMeetingStatus.PLANNED
    stored_payload = dict(meeting.payload or {})
    for key in (
        "company_calendar_synced",
        "company_calendar",
        "company_calendar_item_id",
        "company_calendar_changekey",
        "company_calendar_error",
    ):
        if key in result:
            value = result[key]
            if key == "company_calendar_error" and not value:
                stored_payload.pop(key, None)
            elif value is not None:
                stored_payload[key] = value
    meeting.payload = stored_payload or None
    await db.flush()
    return result


async def cancel_scheduled_meeting_in_outlook(
    meeting: ScheduledMeeting,
    *,
    message: str = "",
) -> dict[str, Any]:
    if not meeting.outlook_series_id:
        raise ScheduledMeetingOutlookError(
            "Серия не связана с календарём Outlook",
            status_code=409,
        )

    from app.tools.Outlook.cancel_meeting import dispatch_cancel_meeting

    company_item_id, company_changekey = _company_calendar_ids_from_meeting_payload(meeting)
    try:
        return await asyncio.to_thread(
            dispatch_cancel_meeting,
            item_id=meeting.outlook_series_id,
            changekey=meeting.outlook_changekey or "",
            message=message,
            cancel_scope="series",
            company_calendar_item_id=company_item_id,
            company_calendar_changekey=company_changekey,
        )
    except RuntimeError as exc:
        if "уже отменено" in str(exc).lower():
            return {"status": "cancelled", "already_cancelled": True}
        raise ScheduledMeetingOutlookError(str(exc)) from exc


def _company_calendar_ids_from_meeting_payload(meeting: ScheduledMeeting) -> tuple[str | None, str | None]:
    payload = meeting.payload if isinstance(meeting.payload, dict) else {}
    item_id = payload.get("company_calendar_item_id")
    changekey = payload.get("company_calendar_changekey")
    return (
        item_id if isinstance(item_id, str) and item_id.strip() else None,
        changekey if isinstance(changekey, str) else None,
    )


def _merge_company_calendar_into_meeting_payload(
    meeting: ScheduledMeeting,
    meta: dict[str, Any],
) -> None:
    stored = dict(meeting.payload or {})
    for key in (
        "company_calendar_synced",
        "company_calendar",
        "company_calendar_item_id",
        "company_calendar_changekey",
        "company_calendar_error",
    ):
        if key in meta:
            value = meta[key]
            if key == "company_calendar_error" and not value:
                stored.pop(key, None)
            elif value is not None:
                stored[key] = value
    meeting.payload = stored or None


def resync_scheduled_meeting_company_calendar(meeting: ScheduledMeeting) -> dict[str, Any]:
    if not meeting.outlook_series_id:
        raise ScheduledMeetingOutlookError(
            "Серия не связана с календарём Outlook",
            status_code=409,
        )

    from app.tools.Outlook.cancel_meeting import get_meeting_by_id
    from app.tools.Outlook.company_calendar_sync import sync_meeting_to_company_calendar

    config = load_config()
    item = get_meeting_by_id(
        config=config,
        item_id=meeting.outlook_series_id,
        changekey=meeting.outlook_changekey or "",
    )
    company_item_id, company_changekey = _company_calendar_ids_from_meeting_payload(meeting)
    company_meta = sync_meeting_to_company_calendar(
        item,
        config=config,
        company_item_id=company_item_id,
        company_changekey=company_changekey,
    )
    _merge_company_calendar_into_meeting_payload(meeting, company_meta)
    return company_meta


async def resync_scheduled_meeting_company_calendar_async(
    db: AsyncSession,
    meeting: ScheduledMeeting,
) -> dict[str, Any]:
    result = await asyncio.to_thread(resync_scheduled_meeting_company_calendar, meeting)
    await db.flush()
    return result
