"""Поиск более ранних слотов в реестре совещаний после изменения состава."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

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
    REGISTRY_EARLIER_SLOT_MIN_COVERAGE_RATIO,
    SLOT_PREVIEW_MAX_DAYS,
    SLOT_PREVIEW_TIMEOUT_SECONDS,
)
from app.services.meeting_mappers import quorum_slot_is_fully_free
from app.services.meeting_slot import (
    format_slot_label,
    parse_slot_datetime,
    resolve_registry_earlier_slot_window,
)

logger = get_logger(__name__)

EARLIER_SLOT_MESSAGE = (
    "После удаления участников доступны более ранние слоты для совещания"
)


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
        quorum_slots = await asyncio.wait_for(
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
