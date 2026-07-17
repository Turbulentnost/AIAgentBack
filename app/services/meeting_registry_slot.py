"""Поиск более ранних слотов в реестре совещаний после изменения состава."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.agents.meeting_agent.backend import ResolvedParticipant
from app.core.logging import get_logger
from app.models.meeting_registry import MeetingRegistryEntry
from app.models.user import User
from app.schemas.meeting import (
    MeetingRegistryEarlierSlotCandidateRead,
    MeetingRegistryEarlierSlotSuggestionRead,
)
from app.services.meeting_backend import MeetingBackend, MeetingBackendError, MeetingQuorumSlot
from app.services.meeting_constants import (
    QUORUM_MAX_CANDIDATES,
    REGISTRY_COMMON_SLOT_MIN_COVERAGE_RATIO,
    REGISTRY_EARLIER_SLOT_MIN_COVERAGE_RATIO,
    SLOT_PREVIEW_MAX_DAYS,
    SLOT_PREVIEW_TIMEOUT_SECONDS,
)
from app.services.meeting_mappers import quorum_slot_is_fully_free
from app.services.meeting_slot import (
    format_slot_label,
    parse_slot_datetime,
    resolve_registry_common_slot_window,
    resolve_registry_earlier_slot_window,
)
from app.tools.Outlook.find_meeting_slot import build_slot_participant_details
from app.tools.Outlook.send_meeting_invite import load_config

logger = get_logger(__name__)

EARLIER_SLOT_MESSAGE = (
    "После удаления участников доступны более ранние слоты для совещания"
)
ADD_CURRENT_SLOT_MESSAGE = (
    "Новый участник свободен в текущее время совещания. Подтвердите добавление."
)
COMMON_SLOT_MESSAGE = (
    "Для всех участников нет общего свободного времени в текущий слот. "
    "Выберите новое время совещания."
)


@dataclass(frozen=True)
class RegistryCurrentSlotAvailability:
    all_free: bool
    free_count: int
    total_count: int
    participants: list[dict[str, Any]]


def _max_search_days(lower_bound: datetime, upper_bound: datetime) -> int:
    delta_days = (upper_bound.date() - lower_bound.date()).days + 1
    return max(1, min(delta_days, SLOT_PREVIEW_MAX_DAYS))


def _slot_distance_seconds(slot_start: str, anchor: datetime) -> float:
    parsed = parse_slot_datetime(slot_start)
    if parsed is None:
        return float("inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    anchor_utc = anchor.astimezone(timezone.utc) if anchor.tzinfo else anchor.replace(tzinfo=timezone.utc)
    return abs((parsed.astimezone(timezone.utc) - anchor_utc).total_seconds())


def _filter_and_sort_candidates(
    slots: list[MeetingQuorumSlot],
    *,
    lower_bound: datetime,
    upper_bound: datetime,
) -> list[MeetingQuorumSlot]:
    filtered: list[MeetingQuorumSlot] = []
    for slot in slots:
        start_dt = parse_slot_datetime(slot.start)
        if start_dt is None:
            continue
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        lower = lower_bound if lower_bound.tzinfo else lower_bound.replace(tzinfo=timezone.utc)
        upper = upper_bound if upper_bound.tzinfo else upper_bound.replace(tzinfo=timezone.utc)
        if start_dt < lower or start_dt >= upper:
            continue
        filtered.append(slot)

    filtered.sort(key=lambda item: _slot_distance_seconds(item.start, upper_bound))
    return filtered


def _filter_fully_free_candidates(slots: list[MeetingQuorumSlot]) -> list[MeetingQuorumSlot]:
    """Оставляет только слоты, где свободны все оставшиеся участники."""
    return [slot for slot in slots if quorum_slot_is_fully_free(slot)]


def _candidate_read(slot: MeetingQuorumSlot) -> MeetingRegistryEarlierSlotCandidateRead:
    return MeetingRegistryEarlierSlotCandidateRead(
        slot_start=slot.start,
        slot_end=slot.end,
        slot_label=format_slot_label(slot.start, slot.end),
        coverage_ratio=slot.coverage_ratio,
        free_attendees_count=slot.free_count,
        total_attendees_count=slot.total_count,
    )


async def suggest_earlier_slots_after_removal(
    *,
    entry: MeetingRegistryEntry,
    remaining_attendee_emails: list[str],
    memo_detail: dict | None,
    current_user: User,
    backend: MeetingBackend,
) -> MeetingRegistryEarlierSlotSuggestionRead | None:
    """Ищет более ранние слоты для оставшихся участников в окне [желаемая дата, текущий слот)."""
    emails = [email.strip() for email in remaining_attendee_emails if email and email.strip()]
    if not emails:
        return None

    window = resolve_registry_earlier_slot_window(entry, memo_detail)
    if window is None:
        return None

    participants = [
        ResolvedParticipant(fio=email, email=email, found=True) for email in emails
    ]
    max_days = _max_search_days(window.lower_bound, window.upper_bound)

    try:
        quorum_result = await asyncio.wait_for(
            backend.find_quorum_slots(
                memo=None,
                participants=participants,
                planned_start=window.search_from_label,
                duration_minutes=window.duration_minutes,
                current_user=current_user,
                max_days=max_days,
                min_coverage_ratio=REGISTRY_EARLIER_SLOT_MIN_COVERAGE_RATIO,
                max_results=QUORUM_MAX_CANDIDATES,
                verify_calendar=True,
                quiet=True,
                latest_allowed=window.search_until_label,
                raise_if_empty=False,
            ),
            timeout=SLOT_PREVIEW_TIMEOUT_SECONDS,
        )
        quorum_slots = quorum_result.slots
    except TimeoutError:
        logger.warning(
            "meeting.registry_earlier_slot_timeout",
            ref_key=entry.memo_ref_key,
            timeout_seconds=SLOT_PREVIEW_TIMEOUT_SECONDS,
        )
        return None
    except MeetingBackendError as exc:
        logger.warning(
            "meeting.registry_earlier_slot_failed",
            ref_key=entry.memo_ref_key,
            error=str(exc),
        )
        return None
    except Exception as exc:
        logger.warning(
            "meeting.registry_earlier_slot_error",
            ref_key=entry.memo_ref_key,
            error=str(exc),
        )
        return None

    ranked = _filter_fully_free_candidates(
        _filter_and_sort_candidates(
            quorum_slots,
            lower_bound=window.lower_bound,
            upper_bound=window.upper_bound,
        )
    )
    if not ranked:
        return None

    return MeetingRegistryEarlierSlotSuggestionRead(
        message=EARLIER_SLOT_MESSAGE,
        current_slot_label=window.current_slot_label,
        search_from=window.search_from_label,
        search_until=window.search_until_label,
        candidates=[_candidate_read(slot) for slot in ranked],
    )


async def resolve_registry_current_slot_availability(
    *,
    entry: MeetingRegistryEntry,
    attendee_details: list[dict[str, str]],
) -> RegistryCurrentSlotAvailability | None:
    """Статус каждого участника в текущем слоте совещания реестра."""
    if entry.slot_start is None or entry.slot_end is None:
        return None

    attendees = [
        {"fio": item["fio"], "email": item["email"], "role": item.get("role", "participant")}
        for item in attendee_details
        if item.get("email")
    ]
    if not attendees:
        return None

    slot_start = entry.slot_start
    slot_end = entry.slot_end
    if slot_start.tzinfo is None:
        slot_start = slot_start.replace(tzinfo=timezone.utc)
    if slot_end.tzinfo is None:
        slot_end = slot_end.replace(tzinfo=timezone.utc)

    try:
        payload = await asyncio.to_thread(
            build_slot_participant_details,
            config=load_config(),
            attendees=attendees,
            slot_start=slot_start,
            slot_end=slot_end,
            include_company_calendar=True,
            verify_personal_calendars=False,
        )
    except Exception as exc:
        logger.warning(
            "meeting.registry_add_slot_check_failed",
            ref_key=entry.memo_ref_key,
            error=str(exc),
        )
        return None

    participants = payload.get("participants") or []
    if not participants:
        return None
    free_count = sum(1 for participant in participants if participant.get("status") == "free")
    total_count = len(participants)
    return RegistryCurrentSlotAvailability(
        all_free=free_count == total_count and total_count > 0,
        free_count=free_count,
        total_count=total_count,
        participants=participants,
    )


async def check_registry_attendees_free_at_current_slot(
    *,
    entry: MeetingRegistryEntry,
    attendee_details: list[dict[str, str]],
) -> bool:
    """Проверяет, свободны ли все участники в текущем слоте совещания реестра."""
    availability = await resolve_registry_current_slot_availability(
        entry=entry,
        attendee_details=attendee_details,
    )
    return bool(availability and availability.all_free)


async def suggest_common_slots_after_add(
    *,
    entry: MeetingRegistryEntry,
    attendee_emails: list[str],
    memo_detail: dict | None,
    current_user: User,
    backend: MeetingBackend,
) -> MeetingRegistryEarlierSlotSuggestionRead | None:
    """Ищет общий свободный слот для полного состава, начиная с текущего времени совещания."""
    del memo_detail
    emails = [email.strip() for email in attendee_emails if email and email.strip()]
    if not emails:
        return None

    window = resolve_registry_common_slot_window(entry)
    if window is None:
        return None

    participants = [
        ResolvedParticipant(fio=email, email=email, found=True) for email in emails
    ]
    max_days = _max_search_days(window.lower_bound, window.upper_bound)

    try:
        quorum_result = await asyncio.wait_for(
            backend.find_quorum_slots(
                memo=None,
                participants=participants,
                planned_start=window.search_from_label,
                duration_minutes=window.duration_minutes,
                current_user=current_user,
                max_days=max_days,
                min_coverage_ratio=REGISTRY_COMMON_SLOT_MIN_COVERAGE_RATIO,
                max_results=QUORUM_MAX_CANDIDATES,
                verify_calendar=True,
                quiet=True,
                raise_if_empty=False,
            ),
            timeout=SLOT_PREVIEW_TIMEOUT_SECONDS,
        )
        quorum_slots = quorum_result.slots
    except TimeoutError:
        logger.warning(
            "meeting.registry_common_slot_timeout",
            ref_key=entry.memo_ref_key,
            timeout_seconds=SLOT_PREVIEW_TIMEOUT_SECONDS,
        )
        return None
    except MeetingBackendError as exc:
        logger.warning(
            "meeting.registry_common_slot_failed",
            ref_key=entry.memo_ref_key,
            error=str(exc),
        )
        return None
    except Exception as exc:
        logger.warning(
            "meeting.registry_common_slot_error",
            ref_key=entry.memo_ref_key,
            error=str(exc),
        )
        return None

    ranked = _filter_fully_free_candidates(
        _filter_and_sort_candidates(
            quorum_slots,
            lower_bound=window.lower_bound,
            upper_bound=window.upper_bound,
        )
    )
    if not ranked:
        return None

    return MeetingRegistryEarlierSlotSuggestionRead(
        message=COMMON_SLOT_MESSAGE,
        current_slot_label=window.current_slot_label,
        search_from=window.search_from_label,
        search_until=window.search_until_label,
        candidates=[_candidate_read(slot) for slot in ranked],
    )
