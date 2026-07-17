from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ScheduledMeetingStatus
from app.models.scheduled_meeting import ScheduledMeeting
from app.services.scheduled_meeting_outlook import (
    ScheduledMeetingOutlookError,
    _company_calendar_ids_from_meeting_payload,
    _invite_body,
    _merge_company_calendar_into_meeting_payload,
    resolve_attendees,
)
from app.tools.Outlook.cancel_meeting import get_meeting_by_id
from app.tools.Outlook.send_meeting_invite import load_config
from app.tools.Outlook.update_meeting_attendees import update_meeting_attendees_item

logger = logging.getLogger(__name__)


def _sync_series_participants_in_outlook(
    meeting: ScheduledMeeting,
    *,
    add_emails: list[str] | None = None,
    remove_emails: list[str] | None = None,
    calendar_invite_body: str,
) -> dict[str, Any]:
    add_list = list(add_emails or [])
    remove_list = list(remove_emails or [])
    if not add_list and not remove_list:
        raise ScheduledMeetingOutlookError(
            "Не указаны e-mail участников для изменения состава",
            status_code=400,
        )
    if not meeting.outlook_series_id:
        raise ScheduledMeetingOutlookError(
            "Серия не связана с календарём Outlook",
            status_code=409,
        )
    if meeting.status != ScheduledMeetingStatus.PLANNED:
        raise ScheduledMeetingOutlookError(
            "Изменение участников в Outlook доступно только для распланированной серии",
            status_code=409,
        )

    if add_list and remove_list:
        action = "participants_updated"
    elif add_list:
        action = "participants_added"
    else:
        action = "participants_removed"

    config = load_config()
    item = get_meeting_by_id(
        config=config,
        item_id=meeting.outlook_series_id,
        changekey=meeting.outlook_changekey or "",
    )
    company_item_id, company_changekey = _company_calendar_ids_from_meeting_payload(meeting)
    result = update_meeting_attendees_item(
        item,
        add=add_list or None,
        remove=remove_list or None,
        attendees_scope="series",
        config=config,
        company_calendar_item_id=company_item_id,
        company_calendar_changekey=company_changekey,
        calendar_invite_body=calendar_invite_body,
    )
    changekey = getattr(item, "changekey", None)
    if isinstance(changekey, str) and changekey.strip():
        result["outlook_changekey"] = changekey
    _merge_company_calendar_into_meeting_payload(meeting, result)
    return {
        "action": action,
        **result,
    }


async def sync_series_participants_in_outlook(
    db: AsyncSession,
    meeting: ScheduledMeeting,
    *,
    add_emails: list[str] | None = None,
    remove_emails: list[str] | None = None,
) -> dict[str, Any]:
    await db.flush()
    await db.refresh(meeting, attribute_names=["participants"])
    for participant in meeting.participants:
        await db.refresh(participant, attribute_names=["position"])

    attendees = await resolve_attendees(db, meeting)
    calendar_invite_body = _invite_body(meeting, attendees)

    return await asyncio.to_thread(
        _sync_series_participants_in_outlook,
        meeting,
        add_emails=add_emails,
        remove_emails=remove_emails,
        calendar_invite_body=calendar_invite_body,
    )


async def add_participants_to_series_in_outlook(
    db: AsyncSession,
    meeting: ScheduledMeeting,
    *,
    add_emails: list[str],
) -> dict[str, Any]:
    return await sync_series_participants_in_outlook(
        db,
        meeting,
        add_emails=add_emails,
    )


async def remove_participants_from_series_in_outlook(
    db: AsyncSession,
    meeting: ScheduledMeeting,
    *,
    remove_emails: list[str],
) -> dict[str, Any]:
    return await sync_series_participants_in_outlook(
        db,
        meeting,
        remove_emails=remove_emails,
    )
