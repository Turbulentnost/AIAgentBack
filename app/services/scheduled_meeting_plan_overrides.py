from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.core.config import settings
from app.models.scheduled_meeting import ScheduledMeeting
from app.schemas.scheduled_meeting import ScheduledMeetingPlanOverride
from app.services.scheduled_meeting_occurrences import build_occurrences_from_rule
from app.tools.Outlook.cancel_meeting import dispatch_cancel_meeting
from app.tools.Outlook.reschedule_meeting import dispatch_reschedule_meeting
from app.tools.Outlook.send_meeting_invite import parse_start

logger = logging.getLogger(__name__)


class ScheduledMeetingPlanOverrideError(Exception):
    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def _format_slot_label(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


async def apply_plan_overrides(
    meeting: ScheduledMeeting,
    overrides: list[ScheduledMeetingPlanOverride],
) -> None:
    if not overrides:
        return

    timezone_name = settings.OUTLOOK_TIMEZONE
    occurrences = {
        item.occurrence_date: item
        for item in build_occurrences_from_rule(
            meeting,
            range_start=meeting.series_start_date,
            range_end=meeting.series_end_date,
        )
    }
    subject = (meeting.title or "").strip()
    if not subject:
        raise ScheduledMeetingPlanOverrideError("У серии не задана тема для переноса/отмены")

    for override in overrides:
        occurrence = occurrences.get(override.occurrence_date)
        if occurrence is None:
            raise ScheduledMeetingPlanOverrideError(
                f"Дата {override.occurrence_date.isoformat()} отсутствует в серии",
                status_code=400,
            )
        original_start = _format_slot_label(occurrence.slot_start)

        if override.action == "keep":
            continue

        if override.action == "skip":
            try:
                await asyncio.to_thread(
                    dispatch_cancel_meeting,
                    subject=subject,
                    start=original_start,
                    cancel_scope="occurrence",
                    timezone=timezone_name,
                )
            except Exception as exc:
                logger.exception(
                    "scheduled_plan_override_skip_failed meeting_id=%s date=%s",
                    meeting.id,
                    override.occurrence_date,
                )
                raise ScheduledMeetingPlanOverrideError(
                    "Серия создана в Outlook, но не удалось пропустить конфликтную дату "
                    f"{override.occurrence_date.isoformat()}: {exc}"
                ) from exc
            continue

        new_start_raw = (override.new_start or "").strip()
        new_start_dt = parse_start(new_start_raw, timezone_name)
        new_end_dt = new_start_dt + timedelta(minutes=meeting.duration_minutes)
        try:
            await asyncio.to_thread(
                dispatch_reschedule_meeting,
                subject=subject,
                start=original_start,
                new_start=_format_slot_label(new_start_dt),
                new_end=_format_slot_label(new_end_dt),
                duration_minutes=meeting.duration_minutes,
                reschedule_scope="occurrence",
                timezone=timezone_name,
            )
        except Exception as exc:
            logger.exception(
                "scheduled_plan_override_shift_failed meeting_id=%s date=%s",
                meeting.id,
                override.occurrence_date,
            )
            raise ScheduledMeetingPlanOverrideError(
                "Серия создана в Outlook, но не удалось сдвинуть конфликтную дату "
                f"{override.occurrence_date.isoformat()}: {exc}"
            ) from exc
