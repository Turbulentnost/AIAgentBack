from __future__ import annotations

import asyncio
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


async def resolve_attendees(
    db: AsyncSession,
    meeting: ScheduledMeeting,
) -> list[tuple[str, str]]:
    from app.tools.onec.lookup_email_by_fio import lookup_email_by_fio

    attendees: list[tuple[str, str]] = []
    seen: set[str] = set()
    unresolved: list[str] = []

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
        if not address:
            continue
        users_by_position.setdefault(position_key, []).append((fio, address))

    for participant in sorted(meeting.participants, key=lambda item: item.sort_order):
        title = (
            participant.department.name.strip()
            if participant.department is not None and participant.department.name
            else ""
        )
        if not title:
            unresolved.append(str(participant.department_id))
            continue

        normalized_title = normalize_position_title(title)
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
                if not address:
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
    await db.flush()
    return result
