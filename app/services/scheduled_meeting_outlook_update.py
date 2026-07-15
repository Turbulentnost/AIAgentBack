from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from exchangelib.items import SEND_ONLY_TO_ALL
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ScheduledMeetingStatus
from app.models.scheduled_meeting import ScheduledMeeting
from app.services.scheduled_meeting_outlook import (
    ScheduledMeetingOutlookError,
    _build_recurrence,
    _combine_start,
)
from app.tools.Outlook.cancel_meeting import get_meeting_by_id
from app.tools.Outlook.company_calendar_sync import sync_meeting_to_company_calendar
from app.tools.Outlook.outlook_meeting_link import calendar_item_outlook_meta
from app.tools.Outlook.send_meeting_invite import load_config

logger = logging.getLogger(__name__)


def _update_series_recurrence_end(
    meeting: ScheduledMeeting,
    *,
    new_end_date: date,
) -> dict[str, Any]:
    if not meeting.outlook_series_id:
        raise ScheduledMeetingOutlookError(
            "Серия не связана с календарём Outlook",
            status_code=409,
        )
    if meeting.status != ScheduledMeetingStatus.PLANNED:
        raise ScheduledMeetingOutlookError(
            "Изменение срока в Outlook доступно только для распланированной серии",
            status_code=409,
        )
    if new_end_date < meeting.series_start_date:
        raise ScheduledMeetingOutlookError(
            "Дата окончания серии не может быть раньше даты начала",
            status_code=400,
        )

    config = load_config()
    item = get_meeting_by_id(
        config=config,
        item_id=meeting.outlook_series_id,
        changekey=meeting.outlook_changekey or "",
    )
    recurrence = getattr(item, "recurrence", None)
    if recurrence is None:
        raise ScheduledMeetingOutlookError(
            "У совещания в Outlook не задано правило повторения",
            status_code=400,
        )

    previous_end = meeting.series_end_date
    meeting.series_end_date = new_end_date
    try:
        start = _combine_start(meeting, config.timezone)
        item.recurrence = _build_recurrence(meeting, start)
        item.save(update_fields=["recurrence"], send_meeting_invitations=SEND_ONLY_TO_ALL)
        company_meta = sync_meeting_to_company_calendar(item, config=config)
        outlook_meta = calendar_item_outlook_meta(item, config)
    finally:
        meeting.series_end_date = previous_end

    action = (
        "series_end_extended"
        if new_end_date > previous_end
        else "series_end_shortened"
        if new_end_date < previous_end
        else "series_end_unchanged"
    )
    return {
        "action": action,
        "previous_end_date": previous_end.isoformat(),
        "new_end_date": new_end_date.isoformat(),
        **outlook_meta,
        **company_meta,
    }


async def update_series_end_date_in_outlook(
    _db: AsyncSession,
    meeting: ScheduledMeeting,
    *,
    new_end_date: date,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _update_series_recurrence_end,
        meeting,
        new_end_date=new_end_date,
    )
