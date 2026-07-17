"""Slot preview/detail для модалки meeting agent."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.meeting_registry import MeetingRegistryEntry
from app.models.user import User
from app.schemas.meeting import (
    MeetingAgentSlotDetailRead,
    MeetingAgentSlotDetailRequest,
    MeetingAgentSlotPreviewRead,
    MeetingAgentSlotPreviewRequest,
    MeetingAttendeeRead,
    MeetingSlotCoverageRead,
    MeetingSlotParticipantStatusRead,
    MeetingSlotRescheduleRecommendationRead,
    MeetingSlotRoomStatusRead,
)
from app.services.meeting_agent_errors import (
    format_calendar_error,
    format_email_lookup_error,
    format_missing_emails_error,
    format_no_slot_error,
    format_partial_slot_preview_note,
    format_participants_missing_error,
    format_reschedule_suggestions_note,
    format_slot_preview_timeout_error,
)
from app.services.meeting_agent_slot_responses import (
    agent_slot_detail_error,
    agent_slot_preview_error,
)
from app.services.meeting_attendee_priority import (
    priority_role_label,
    weight_for_priority_role,
)
from app.services.meeting_attendees import (
    collect_attendees_from_detail,
    collect_attendees_from_registry_entry,
    emails_by_fio_from_detail,
    person_from_detail_by_fio,
)
from app.services.meeting_backend import (
    MeetingBackend,
    MeetingBackendError,
    MeetingMemo,
    MeetingSlot,
    MeetingSlotConflict,
    ResolvedParticipant,
    _duration_from_memo,
    _normalize_memo,
)
from app.services.meeting_constants import (
    ATTENDEE_NEAREST_SLOT_TIMEOUT_SECONDS,
    COMPANY_CALENDAR_TIMEOUT_SECONDS,
    QUORUM_MAX_CANDIDATES,
    QUORUM_MIN_COVERAGE_RATIO,
    QUORUM_VERIFY_TOP_N,
    SLOT_DETAIL_TIMEOUT_SECONDS,
    SLOT_PREVIEW_MAX_DAYS,
    SLOT_PREVIEW_TIMEOUT_SECONDS,
)
from app.services.meeting_duration import resolve_duration_minutes
from app.services.meeting_exceptions import MeetingServiceError
from app.services.meeting_mappers import (
    attendee_weights_from_attendees,
    conflict_read,
    coverage_read,
    email_roles_from_attendees,
    leadership_required_emails,
    participant_status_read,
    quorum_slot_is_fully_free,
    quorum_slot_read,
    reschedule_recommendation_from_conflict,
    room_status_read,
    slot_read,
)
from app.services.meeting_invite_format import (
    place_from_detail,
    resolve_room_for_location,
)
from app.services.meeting_memo_cache import (
    MeetingMemoCacheService,
    MemoCacheMissError,
    detail_to_memo_document,
)
from app.services.meeting_slot import (
    format_planned_start_for_search,
    format_attendee_nearest_slot_search_start,
    format_search_start_from_meeting_date,
    format_slot_label,
    parse_slot_datetime,
    slot_duration_minutes,
)
from app.tools.Outlook.find_meeting_slot import (
    build_slot_participant_details,
    dispatch_find_attendee_nearest_slots,
    dispatch_find_meeting_slot,
)
from app.tools.Outlook.meeting_rooms import check_rooms_status
from app.tools.Outlook.send_meeting_invite import load_config

logger = get_logger(__name__)


async def _fetch_attendee_nearest_slots_bulk(
    *,
    emails: list[str],
    planned_start: str,
    duration_minutes: int,
    max_days: int,
) -> dict[str, MeetingSlot | None]:
    """Справочные ближайшие слоты: один bulk Free/Busy на всех участников."""
    if not emails:
        return {}

    payload = await asyncio.wait_for(
        asyncio.to_thread(
            dispatch_find_attendee_nearest_slots,
            attendees=emails,
            preferred=planned_start,
            duration_minutes=duration_minutes,
            max_days=max_days,
            quiet=True,
        ),
        timeout=ATTENDEE_NEAREST_SLOT_TIMEOUT_SECONDS,
    )

    slots_by_email: dict[str, MeetingSlot | None] = {}
    for email in emails:
        slot_payload = payload.get(email)
        if not slot_payload:
            slots_by_email[email] = None
            continue
        slot_start = slot_payload.get("slot_start")
        slot_end = slot_payload.get("slot_end")
        if not slot_start or not slot_end:
            slots_by_email[email] = None
            continue
        slots_by_email[email] = MeetingSlot(
            start=slot_start,
            end=slot_end,
            confidence=0.7,
        )
    return slots_by_email


def _build_room_slot_status(
    *,
    detail: dict[str, Any],
    config: Any,
    slot_start: Any,
    slot_end: Any,
) -> dict[str, Any]:
    """Проверяет занятость переговорной из СЗ на выбранный слот."""
    location = place_from_detail(detail)
    if not location:
        return {
            "name": "Не указана",
            "email": None,
            "status": "unknown",
            "status_label": "не указана",
            "available": None,
        }

    room = resolve_room_for_location(location)
    if room is None:
        return {
            "name": location,
            "email": None,
            "status": "unknown",
            "status_label": "не найдена в справочнике",
            "available": None,
        }

    try:
        rows = check_rooms_status(
            config=config,
            rooms=[room],
            slot_start=slot_start,
            slot_end=slot_end,
        )
    except Exception as exc:
        logger.warning(
            "meeting.slot_detail.room_check_failed",
            room=room.get("name"),
            error=str(exc),
        )
        return {
            "name": room["name"],
            "email": room.get("email"),
            "status": "unknown",
            "status_label": "не удалось проверить",
            "available": None,
            "calendar_access_error": str(exc),
        }

    row = rows[0]
    is_free = row.get("status") == "free"
    return {
        "name": row.get("name") or room["name"],
        "email": row.get("email") or room.get("email"),
        "status": "free" if is_free else "busy",
        "status_label": row.get("status_label") or ("свободна" if is_free else "занята"),
        "available": is_free,
    }


def _room_participant_payload(room_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "fio": room_status["name"],
        "email": room_status.get("email"),
        "role": "room",
        "status": room_status.get("status") or "unknown",
        "blocking_events": [],
        "calendar_access_error": room_status.get("calendar_access_error"),
    }


def participants_busy(
    participants: list[MeetingSlotParticipantStatusRead],
) -> bool:
    """Занят ли хотя бы один участник (без переговорной)."""
    return any(
        item.status == "busy" for item in participants if item.role != "room"
    )


def _store_availability_cache_id(
    memo_ref_key: str,
    snapshot_payload: dict[str, Any] | None,
) -> str | None:
    if not snapshot_payload:
        return None
    from app.services.slot_availability_cache import (
        store_availability_snapshot,
        trim_snapshot_for_cache,
    )

    return store_availability_snapshot(
        trim_snapshot_for_cache(
            {
                **snapshot_payload,
                "memo_ref_key": memo_ref_key,
            }
        )
    )


def build_slot_detail_availability(
    participants: list[MeetingSlotParticipantStatusRead],
    *,
    room: MeetingSlotRoomStatusRead | None,
) -> tuple[bool, list[MeetingSlotRescheduleRecommendationRead]]:
    """Определяет доступность слота и список встреч для переноса."""
    people = [item for item in participants if item.role != "room"]
    slot_available = all(item.status == "free" for item in people)
    if room is not None and room.status == "busy":
        slot_available = False

    recommendations: list[MeetingSlotRescheduleRecommendationRead] = []
    for participant in people:
        if participant.status != "busy":
            continue
        for event in participant.blocking_events:
            recommendations.append(
                MeetingSlotRescheduleRecommendationRead(
                    participant_fio=participant.fio,
                    event_label=event.event_label,
                    event_time_label=event.event_time_label,
                    reschedule_hint_label=event.reschedule_hint_label,
                )
            )

    if room is not None and room.status == "busy":
        recommendations.append(
            MeetingSlotRescheduleRecommendationRead(
                participant_fio=room.name,
                event_label="Переговорная занята",
                event_time_label=None,
                reschedule_hint_label=None,
            )
        )

    return slot_available, recommendations


def _reschedule_recommendation_key(
    item: MeetingSlotRescheduleRecommendationRead,
) -> tuple[str | None, str | None, str | None]:
    return (item.participant_fio, item.event_label, item.event_time_label)


def merge_reschedule_recommendations(
    primary: list[MeetingSlotRescheduleRecommendationRead],
    extra: list[MeetingSlotRescheduleRecommendationRead],
) -> list[MeetingSlotRescheduleRecommendationRead]:
    seen = {_reschedule_recommendation_key(item) for item in primary}
    merged = list(primary)
    for item in extra:
        key = _reschedule_recommendation_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


class MeetingAgentSlotService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        backend_factory: Callable[[], MeetingBackend] | None = None,
    ) -> None:
        self.db = db
        self._backend_factory = backend_factory

    def _backend(self) -> MeetingBackend:
        if self._backend_factory is not None:
            return self._backend_factory()
        return MeetingBackend(self.db)

    async def _resolve_memo_attendees(
        self,
        detail: dict,
        *,
        backend: MeetingBackend,
        current_user: User,
        attendee_specs: list[tuple[str, str]] | None = None,
    ) -> tuple[MeetingMemo, list[ResolvedParticipant], list[MeetingAttendeeRead], list[str]]:
        specs = attendee_specs or collect_attendees_from_detail(detail)
        if not specs:
            raise MeetingServiceError(
                "В заявке нет участников, инициатора или руководителя для отправки приглашений"
            )

        memo = _normalize_memo(detail_to_memo_document(detail))
        registry_mode = attendee_specs is not None
        if registry_mode:
            cached_emails: dict[str, str] = {}
            need_lookup = [fio for fio, _role in specs]
        else:
            cached_emails = emails_by_fio_from_detail(detail)
            need_lookup = [fio for fio, _role in specs if fio not in cached_emails]
        resolved_lookup = (
            await backend.resolve_participants(need_lookup, current_user=current_user)
            if need_lookup
            else []
        )
        resolved_by_fio = {item.fio: item for item in resolved_lookup}

        attendees: list[MeetingAttendeeRead] = []
        missing_emails: list[str] = []
        resolved: list[ResolvedParticipant] = []
        for fio, priority_role in specs:
            cached_email = cached_emails.get(fio)
            match = resolved_by_fio.get(fio)
            email = cached_email or (match.email if match else None)
            found = bool(email)
            if not found:
                missing_emails.append(fio)
            else:
                resolved.append(ResolvedParticipant(fio=fio, email=email, found=True))
            attendees.append(
                MeetingAttendeeRead(
                    fio=fio,
                    email=email,
                    role=priority_role,
                    role_label=priority_role_label(priority_role),
                    weight=weight_for_priority_role(
                        priority_role,
                        None if registry_mode else person_from_detail_by_fio(detail, fio),
                    ),
                    required_for_slot=found,
                    found=found,
                )
            )
        return memo, resolved, attendees, missing_emails

    async def _company_calendar_reschedule_recommendations(
        self,
        *,
        resolved: list[ResolvedParticipant],
        attendees: list[MeetingAttendeeRead],
        backend: MeetingBackend,
        planned_start: str,
        duration_minutes: int,
        current_user: User,
    ) -> list[MeetingSlotRescheduleRecommendationRead]:
        if not resolved:
            return []
        try:
            conflicts = await asyncio.wait_for(
                backend.find_company_calendar_reschedule_candidates(
                    participants=resolved,
                    attendee_roles=email_roles_from_attendees(attendees),
                    required_attendee_emails=leadership_required_emails(attendees) or None,
                    attendee_weights=attendee_weights_from_attendees(attendees),
                    planned_start=planned_start,
                    duration_minutes=duration_minutes,
                    max_days=SLOT_PREVIEW_MAX_DAYS,
                    current_user=current_user,
                ),
                timeout=COMPANY_CALENDAR_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "meeting.slot_detail.company_calendar_timeout",
                timeout_seconds=COMPANY_CALENDAR_TIMEOUT_SECONDS,
            )
            return []
        except MeetingBackendError as exc:
            logger.warning(
                "meeting.slot_detail.company_calendar_failed",
                error=str(exc),
            )
            return []
        except Exception as exc:
            logger.warning(
                "meeting.slot_detail.company_calendar_failed",
                error=str(exc),
            )
            return []

        return [
            reschedule_recommendation_from_conflict(conflict, attendees=attendees)
            for conflict in conflicts
        ]

    async def _enrich_attendees_with_nearest_slots(
        self,
        attendees: list[MeetingAttendeeRead],
        *,
        backend: MeetingBackend,
        memo: MeetingMemo | dict[str, Any] | None,
        search_start: str | None,
        duration_minutes: int,
        current_user: User,
        max_days: int = SLOT_PREVIEW_MAX_DAYS,
    ) -> list[MeetingAttendeeRead]:
        del backend, memo, current_user
        if not search_start:
            return attendees

        emails = [
            attendee.email.strip()
            for attendee in attendees
            if attendee.found and attendee.email
        ]
        if not emails:
            return attendees

        try:
            nearest_by_email = await _fetch_attendee_nearest_slots_bulk(
                emails=emails,
                planned_start=search_start,
                duration_minutes=duration_minutes,
                max_days=max_days,
            )
        except TimeoutError:
            logger.info(
                "meeting.slot_preview.attendee_slots_failed",
                emails=emails,
                error="timeout",
            )
            return attendees
        except Exception as exc:
            logger.info(
                "meeting.slot_preview.attendee_slots_failed",
                emails=emails,
                error=str(exc),
            )
            return attendees

        enriched: list[MeetingAttendeeRead] = []
        for attendee in attendees:
            if not attendee.found or not attendee.email:
                enriched.append(attendee)
                continue
            slot = nearest_by_email.get(attendee.email.strip())
            if slot is None:
                enriched.append(attendee)
                continue
            enriched.append(
                attendee.model_copy(
                    update={
                        "nearest_slot_start": slot.start,
                        "nearest_slot_end": slot.end,
                        "nearest_slot_label": format_slot_label(slot.start, slot.end),
                    }
                )
            )
        return enriched

    async def suggest_agent_slot(
        self,
        memo_ref_key: str,
        payload: MeetingAgentSlotPreviewRequest,
        *,
        current_user: User,
        attendee_specs: list[tuple[str, str]] | None = None,
    ) -> MeetingAgentSlotPreviewRead:
        """Ближайший слот для модалки «Запустить агента»: участники + инициатор + руководитель."""
        normalized_ref = memo_ref_key.strip().lower()
        try:
            detail, _fetched_at, _from_cache = await MeetingMemoCacheService().get_memo_detail_for_agent(
                normalized_ref
            )
        except MemoCacheMissError as exc:
            return agent_slot_preview_error(
                normalized_ref,
                message=str(exc),
                error_stage="onec",
            )

        backend = self._backend()
        application = detail.get("application") or {}
        try:
            memo, resolved, attendees, missing_emails = await self._resolve_memo_attendees(
                detail,
                backend=backend,
                current_user=current_user,
                attendee_specs=attendee_specs,
            )
        except MeetingServiceError:
            duration = payload.duration_minutes or application.get("duration_minutes") or 60
            return agent_slot_preview_error(
                normalized_ref,
                message=format_participants_missing_error(),
                duration_minutes=duration,
                error_stage="participants",
            )
        except MeetingBackendError as exc:
            duration = payload.duration_minutes or application.get("duration_minutes") or 60
            return agent_slot_preview_error(
                normalized_ref,
                message=format_email_lookup_error(exc),
                duration_minutes=duration,
                error_stage="email",
            )

        duration = resolve_duration_minutes(
            payload.duration_minutes,
            application.get("duration_minutes"),
            _duration_from_memo(memo),
        )
        planned_start = (
            (payload.planned_start or "").strip()
            or format_planned_start_for_search(
                application.get("meeting_start"),
                detail.get("queue") or {},
            )
        )
        attendee_search_start = format_attendee_nearest_slot_search_start(
            application.get("meeting_start"),
            detail.get("queue") or {},
        )

        attendees = await self._enrich_attendees_with_nearest_slots(
            attendees,
            backend=backend,
            memo=memo,
            search_start=attendee_search_start,
            duration_minutes=duration,
            current_user=current_user,
        )

        if missing_emails:
            return agent_slot_preview_error(
                normalized_ref,
                message=format_missing_emails_error(missing_emails),
                duration_minutes=duration,
                attendees=attendees,
                missing_emails=missing_emails,
                error_stage="email",
            )

        attendee_roles = email_roles_from_attendees(attendees)
        attendee_weights = attendee_weights_from_attendees(attendees)
        leadership_emails = leadership_required_emails(attendees)

        logger.info(
            "meeting.slot_preview.search",
            memo_ref_key=normalized_ref,
            attendees=len(resolved),
            planned_start=planned_start,
            duration_minutes=duration,
            max_days=SLOT_PREVIEW_MAX_DAYS,
            search_mode="all",
            timeout_seconds=SLOT_PREVIEW_TIMEOUT_SECONDS,
        )
        try:
            find_result = await asyncio.wait_for(
                backend.find_slots(
                    memo=memo,
                    participants=resolved,
                    planned_start=planned_start,
                    duration_minutes=duration,
                    current_user=current_user,
                    max_days=SLOT_PREVIEW_MAX_DAYS,
                    verify_calendar=True,
                    quiet=False,
                    include_timing=True,
                ),
                timeout=SLOT_PREVIEW_TIMEOUT_SECONDS,
            )
            all_free_slots = find_result.slots
            availability_cache_id: str | None = None
            snapshot_payload = find_result.availability_snapshot
            if snapshot_payload:
                from app.services.slot_availability_cache import (
                    store_availability_snapshot,
                    trim_snapshot_for_cache,
                )

                snapshot_payload = trim_snapshot_for_cache(
                    {
                        **snapshot_payload,
                        "memo_ref_key": normalized_ref,
                    }
                )
                availability_cache_id = store_availability_snapshot(snapshot_payload)
            else:
                logger.info(
                    "meeting.slot_preview.no_availability_snapshot",
                    memo_ref_key=normalized_ref,
                )
        except TimeoutError:
            return agent_slot_preview_error(
                normalized_ref,
                message=format_slot_preview_timeout_error(
                    timeout_seconds=SLOT_PREVIEW_TIMEOUT_SECONDS
                ),
                duration_minutes=duration,
                attendees=attendees,
                missing_emails=missing_emails,
                error_stage="calendar",
            )
        except MeetingBackendError as exc:
            all_free_slots = None
            all_slots_error = exc
        except Exception as exc:
            return agent_slot_preview_error(
                normalized_ref,
                message=format_calendar_error(exc),
                duration_minutes=duration,
                attendees=attendees,
                missing_emails=missing_emails,
                error_stage="calendar",
            )
        else:
            all_slots_error = None

        if all_free_slots:
            slot = slot_read(all_free_slots[0])
            total = len(resolved)
            logger.info(
                "meeting.slot_preview.found_all_free",
                memo_ref_key=normalized_ref,
                slot_start=slot.start,
                slot_end=slot.end,
            )
            return MeetingAgentSlotPreviewRead(
                memo_ref_key=normalized_ref,
                slot=slot,
                slot_label=format_slot_label(slot.start, slot.end),
                duration_minutes=duration,
                attendees=attendees,
                missing_emails=missing_emails,
                coverage=MeetingSlotCoverageRead(
                    free=total,
                    total=total,
                    ratio=1.0,
                    weighted_ratio=1.0,
                    required_ok=True,
                ),
                search_mode="all",
                availability_cache_id=availability_cache_id,
            )

        logger.info(
            "meeting.slot_preview.search_partial",
            memo_ref_key=normalized_ref,
            all_slots_error=str(all_slots_error) if all_slots_error else None,
        )
        try:
            quorum_result = await asyncio.wait_for(
                backend.find_quorum_slots(
                    memo=memo,
                    participants=resolved,
                    attendee_roles=attendee_roles,
                    attendee_weights=attendee_weights,
                    required_attendee_emails=leadership_emails or None,
                    planned_start=planned_start,
                    duration_minutes=duration,
                    current_user=current_user,
                    max_days=SLOT_PREVIEW_MAX_DAYS,
                    min_coverage_ratio=QUORUM_MIN_COVERAGE_RATIO,
                    max_results=QUORUM_MAX_CANDIDATES,
                    verify_top_n=QUORUM_VERIFY_TOP_N,
                    verify_calendar=True,
                    quiet=False,
                    include_timing=True,
                ),
                timeout=SLOT_PREVIEW_TIMEOUT_SECONDS,
            )
            quorum_slots = quorum_result.slots
            availability_cache_id = _store_availability_cache_id(
                normalized_ref,
                quorum_result.availability_snapshot,
            )
        except TimeoutError:
            return agent_slot_preview_error(
                normalized_ref,
                message=format_slot_preview_timeout_error(
                    timeout_seconds=SLOT_PREVIEW_TIMEOUT_SECONDS
                ),
                duration_minutes=duration,
                attendees=attendees,
                missing_emails=missing_emails,
                error_stage="calendar",
            )
        except MeetingBackendError as exc:
            message = str(exc)
            if "Quorum-слот не найден" in message or "Свободный слот не найден" in message:
                return await self._agent_slot_preview_no_slot(
                    normalized_ref,
                    duration=duration,
                    attendees=attendees,
                    missing_emails=missing_emails,
                    backend=backend,
                    resolved=resolved,
                    attendee_roles=attendee_roles,
                    attendee_weights=attendee_weights,
                    leadership_emails=leadership_emails,
                    planned_start=planned_start,
                    current_user=current_user,
                )
            return agent_slot_preview_error(
                normalized_ref,
                message=format_calendar_error(exc),
                duration_minutes=duration,
                attendees=attendees,
                missing_emails=missing_emails,
                error_stage="calendar",
            )
        except Exception as exc:
            return agent_slot_preview_error(
                normalized_ref,
                message=format_calendar_error(exc),
                duration_minutes=duration,
                attendees=attendees,
                missing_emails=missing_emails,
                error_stage="calendar",
            )

        if not quorum_slots:
            return await self._agent_slot_preview_no_slot(
                normalized_ref,
                duration=duration,
                attendees=attendees,
                missing_emails=missing_emails,
                backend=backend,
                resolved=resolved,
                attendee_roles=attendee_roles,
                attendee_weights=attendee_weights,
                leadership_emails=leadership_emails,
                planned_start=planned_start,
                current_user=current_user,
            )

        slot_candidates = [
            quorum_slot_read(item, attendees=attendees) for item in quorum_slots
        ]
        recommended = quorum_slots[0]
        if quorum_slot_is_fully_free(recommended):
            slot = slot_read(
                MeetingSlot(
                    start=recommended.start,
                    end=recommended.end,
                    confidence=recommended.confidence,
                )
            )
            logger.info(
                "meeting.slot_preview.found_all_free_via_quorum",
                memo_ref_key=normalized_ref,
                slot_start=slot.start,
                slot_end=slot.end,
            )
            return MeetingAgentSlotPreviewRead(
                memo_ref_key=normalized_ref,
                slot=slot,
                slot_label=format_slot_label(slot.start, slot.end),
                duration_minutes=duration,
                attendees=attendees,
                missing_emails=missing_emails,
                coverage=coverage_read(recommended),
                search_mode="all",
                availability_cache_id=availability_cache_id,
            )

        logger.info(
            "meeting.slot_preview.partial",
            memo_ref_key=normalized_ref,
            slot_start=recommended.start,
            slot_end=recommended.end,
            coverage_ratio=recommended.coverage_ratio,
            conflicts=len(recommended.conflicts),
        )
        return MeetingAgentSlotPreviewRead(
            memo_ref_key=normalized_ref,
            slot=None,
            slot_label=None,
            duration_minutes=duration,
            attendees=attendees,
            missing_emails=missing_emails,
            coverage=coverage_read(recommended),
            conflicts=[
                conflict_read(conflict, attendees=attendees) for conflict in recommended.conflicts
            ],
            slot_candidates=slot_candidates,
            search_mode="partial",
            preview_note=format_partial_slot_preview_note(),
            availability_cache_id=availability_cache_id,
        )

    async def _agent_slot_preview_no_slot(
        self,
        memo_ref_key: str,
        *,
        duration: int,
        attendees: list[MeetingAttendeeRead],
        missing_emails: list[str],
        backend: MeetingBackend,
        resolved: list[ResolvedParticipant],
        attendee_roles: dict[str, str],
        attendee_weights: dict[str, float],
        leadership_emails: list[str],
        planned_start: str,
        current_user: User,
    ) -> MeetingAgentSlotPreviewRead:
        conflicts: list[MeetingSlotConflict] = []
        try:
            conflicts = await backend.find_company_calendar_reschedule_candidates(
                participants=resolved,
                attendee_roles=attendee_roles,
                required_attendee_emails=leadership_emails or None,
                attendee_weights=attendee_weights,
                planned_start=planned_start,
                duration_minutes=duration,
                max_days=SLOT_PREVIEW_MAX_DAYS,
                current_user=current_user,
            )
        except MeetingBackendError as exc:
            logger.warning(
                "meeting.slot_preview.company_calendar_failed",
                memo_ref_key=memo_ref_key,
                error=str(exc),
            )
        except Exception as exc:
            logger.warning(
                "meeting.slot_preview.company_calendar_failed",
                memo_ref_key=memo_ref_key,
                error=str(exc),
            )

        conflict_reads = [
            conflict_read(conflict, attendees=attendees) for conflict in conflicts
        ]
        return agent_slot_preview_error(
            memo_ref_key,
            message=format_no_slot_error(max_days=SLOT_PREVIEW_MAX_DAYS),
            duration_minutes=duration,
            attendees=attendees,
            missing_emails=missing_emails,
            error_stage="no_slot",
            conflicts=conflict_reads,
            preview_note=format_reschedule_suggestions_note(len(conflict_reads)) or None,
        )

    async def suggest_agent_slot_safe(
        self,
        memo_ref_key: str,
        payload: MeetingAgentSlotPreviewRequest,
        *,
        current_user: User,
        attendee_specs: list[tuple[str, str]] | None = None,
    ) -> MeetingAgentSlotPreviewRead:
        try:
            return await self.suggest_agent_slot(
                memo_ref_key,
                payload,
                current_user=current_user,
                attendee_specs=attendee_specs,
            )
        except MeetingServiceError as exc:
            return agent_slot_preview_error(
                memo_ref_key.strip().lower(),
                message=str(exc),
                error_stage="unknown",
            )
        except Exception as exc:
            return agent_slot_preview_error(
                memo_ref_key.strip().lower(),
                message=f"Не удалось подобрать слот: {exc}",
                error_stage="unknown",
            )

    async def get_agent_slot_detail(
        self,
        memo_ref_key: str,
        payload: MeetingAgentSlotDetailRequest,
        *,
        current_user: User,
    ) -> MeetingAgentSlotDetailRead:
        """Детали выбранного слота: статус каждого участника и мешающие встречи."""
        normalized_ref = memo_ref_key.strip().lower()
        slot_start_dt = parse_slot_datetime(payload.slot_start)
        slot_end_dt = parse_slot_datetime(payload.slot_end)
        if slot_start_dt is None or slot_end_dt is None:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message="Некорректный формат slot_start или slot_end",
                error_stage="slot",
            )
        if slot_end_dt <= slot_start_dt:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message="slot_end должно быть позже slot_start",
                error_stage="slot",
            )

        duration = payload.duration_minutes or slot_duration_minutes(
            payload.slot_start,
            payload.slot_end,
        )

        try:
            detail, _fetched_at, _from_cache = await MeetingMemoCacheService().get_memo_detail_for_agent(
                normalized_ref
            )
        except MemoCacheMissError as exc:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=str(exc),
                error_stage="onec",
            )

        backend = self._backend()
        try:
            _memo, _resolved, attendees, missing_emails = await self._resolve_memo_attendees(
                detail,
                backend=backend,
                current_user=current_user,
            )
        except MeetingServiceError:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=format_participants_missing_error(),
                error_stage="participants",
            )
        except MeetingBackendError as exc:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=format_email_lookup_error(exc),
                error_stage="email",
            )

        if missing_emails:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=format_missing_emails_error(missing_emails),
                error_stage="email",
            )

        attendee_payload = [
            {
                "fio": attendee.fio,
                "email": attendee.email,
                "role": attendee.role,
            }
            for attendee in attendees
        ]

        if payload.availability_cache_id:
            logger.info(
                "meeting.slot_detail.ignore_availability_cache",
                memo_ref_key=normalized_ref,
                cache_id=payload.availability_cache_id,
            )

        logger.info(
            "meeting.slot_detail.fetch",
            memo_ref_key=normalized_ref,
            slot_start=payload.slot_start,
            slot_end=payload.slot_end,
            attendees=len(attendee_payload),
            timeout_seconds=SLOT_DETAIL_TIMEOUT_SECONDS,
            reused_cache=False,
        )
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    build_slot_participant_details,
                    config=load_config(),
                    attendees=attendee_payload,
                    slot_start=slot_start_dt,
                    slot_end=slot_end_dt,
                    step_minutes=15,
                    include_company_calendar=True,
                    light_reschedule_hints=True,
                    verify_personal_calendars=False,
                ),
                timeout=SLOT_DETAIL_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=format_slot_preview_timeout_error(
                    timeout_seconds=SLOT_DETAIL_TIMEOUT_SECONDS
                ),
                error_stage="calendar",
            )
        except ValueError as exc:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=str(exc),
                error_stage="slot",
            )
        except Exception as exc:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=format_calendar_error(exc),
                error_stage="calendar",
            )

        participants = [
            participant_status_read(item, attendees=attendees)
            for item in raw.get("participants") or []
        ]
        outlook_config = load_config()
        room_raw = _build_room_slot_status(
            detail=detail,
            config=outlook_config,
            slot_start=slot_start_dt,
            slot_end=slot_end_dt,
        )
        room = room_status_read(room_raw)
        participants.append(
            participant_status_read(_room_participant_payload(room_raw), attendees=[])
        )
        slot_available, reschedule_recommendations = build_slot_detail_availability(
            participants,
            room=room,
        )
        # Детали слота: общий календарь уже в blocking_events (include_company_calendar).
        # Полный 30-дневный скан find_company_calendar_reschedule_candidates здесь не нужен —
        # он даёт ~180 EWS-запросов и таймаутится на медленном Exchange.
        return MeetingAgentSlotDetailRead(
            memo_ref_key=normalized_ref,
            slot_start=payload.slot_start,
            slot_end=payload.slot_end,
            slot_label=format_slot_label(payload.slot_start, payload.slot_end),
            duration_minutes=duration,
            participants=participants,
            room=room,
            slot_available=slot_available,
            reschedule_recommendations=reschedule_recommendations,
        )

    async def get_agent_slot_detail_safe(
        self,
        memo_ref_key: str,
        payload: MeetingAgentSlotDetailRequest,
        *,
        current_user: User,
    ) -> MeetingAgentSlotDetailRead:
        try:
            return await self.get_agent_slot_detail(
                memo_ref_key,
                payload,
                current_user=current_user,
            )
        except MeetingServiceError as exc:
            return agent_slot_detail_error(
                memo_ref_key.strip().lower(),
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=str(exc),
                error_stage="unknown",
            )
        except Exception as exc:
            return agent_slot_detail_error(
                memo_ref_key.strip().lower(),
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=f"Не удалось загрузить детали слота: {exc}",
                error_stage="unknown",
            )

    async def get_registry_slot_detail(
        self,
        entry: MeetingRegistryEntry,
        payload: MeetingAgentSlotDetailRequest,
        *,
        current_user: User,
    ) -> MeetingAgentSlotDetailRead:
        """П.2/3 (ручной): проверка выбранного слота для состава из реестра."""
        normalized_ref = entry.memo_ref_key.strip().lower()
        slot_start_dt = parse_slot_datetime(payload.slot_start)
        slot_end_dt = parse_slot_datetime(payload.slot_end)
        if slot_start_dt is None or slot_end_dt is None:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message="Некорректный формат slot_start или slot_end",
                error_stage="slot",
            )
        if slot_end_dt <= slot_start_dt:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message="slot_end должно быть позже slot_start",
                error_stage="slot",
            )

        duration = payload.duration_minutes or slot_duration_minutes(
            payload.slot_start,
            payload.slot_end,
        )
        attendee_specs = collect_attendees_from_registry_entry(entry)
        if not attendee_specs:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=format_participants_missing_error(),
                error_stage="participants",
            )

        backend = self._backend()
        detail: dict[str, Any] | None = None
        try:
            detail, _, _ = await MeetingMemoCacheService().get_memo_detail_for_agent(
                normalized_ref
            )
        except MemoCacheMissError:
            detail = {"application": {}}

        try:
            _memo, resolved, attendees, missing_emails = await self._resolve_memo_attendees(
                detail,
                backend=backend,
                current_user=current_user,
                attendee_specs=attendee_specs,
            )
        except MeetingServiceError:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=format_participants_missing_error(),
                error_stage="participants",
            )
        except MeetingBackendError as exc:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=format_email_lookup_error(exc),
                error_stage="email",
            )

        if missing_emails:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=format_missing_emails_error(missing_emails),
                error_stage="email",
            )

        attendee_payload = [
            {"fio": attendee.fio, "email": attendee.email, "role": attendee.role}
            for attendee in attendees
        ]

        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    build_slot_participant_details,
                    config=load_config(),
                    attendees=attendee_payload,
                    slot_start=slot_start_dt,
                    slot_end=slot_end_dt,
                    step_minutes=15,
                    include_company_calendar=True,
                    light_reschedule_hints=True,
                    verify_personal_calendars=False,
                ),
                timeout=SLOT_DETAIL_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=format_slot_preview_timeout_error(
                    timeout_seconds=SLOT_DETAIL_TIMEOUT_SECONDS
                ),
                error_stage="calendar",
            )
        except ValueError as exc:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=str(exc),
                error_stage="slot",
            )
        except Exception as exc:
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=format_calendar_error(exc),
                error_stage="calendar",
            )

        participants = [
            participant_status_read(item, attendees=attendees)
            for item in raw.get("participants") or []
        ]
        outlook_config = load_config()
        location = entry.location.strip() if isinstance(entry.location, str) else None
        room = resolve_room_for_location(location)
        if room is None:
            room_raw = {
                "name": location or "Не указана",
                "email": None,
                "status": "unknown",
                "status_label": "не указана" if not location else "не найдена в справочнике",
                "available": None,
            }
        else:
            try:
                rows = check_rooms_status(
                    config=outlook_config,
                    rooms=[room],
                    slot_start=slot_start_dt,
                    slot_end=slot_end_dt,
                )
                row = rows[0]
                is_free = row.get("status") == "free"
                room_raw = {
                    "name": row.get("name") or room["name"],
                    "email": row.get("email") or room.get("email"),
                    "status": "free" if is_free else "busy",
                    "status_label": row.get("status_label") or ("свободна" if is_free else "занята"),
                    "available": is_free,
                }
            except Exception as exc:
                logger.warning(
                    "meeting.registry_slot_detail.room_check_failed",
                    ref_key=normalized_ref,
                    error=str(exc),
                )
                room_raw = {
                    "name": room["name"],
                    "email": room.get("email"),
                    "status": "unknown",
                    "status_label": "не удалось проверить",
                    "available": None,
                    "calendar_access_error": str(exc),
                }

        room_status = room_status_read(room_raw)
        participants.append(
            participant_status_read(_room_participant_payload(room_raw), attendees=[])
        )
        slot_available, reschedule_recommendations = build_slot_detail_availability(
            participants,
            room=room_status,
        )
        return MeetingAgentSlotDetailRead(
            memo_ref_key=normalized_ref,
            slot_start=payload.slot_start,
            slot_end=payload.slot_end,
            slot_label=format_slot_label(payload.slot_start, payload.slot_end),
            duration_minutes=duration,
            participants=participants,
            room=room_status,
            slot_available=slot_available,
            reschedule_recommendations=reschedule_recommendations,
        )

    async def get_registry_slot_detail_safe(
        self,
        entry: MeetingRegistryEntry,
        payload: MeetingAgentSlotDetailRequest,
        *,
        current_user: User,
    ) -> MeetingAgentSlotDetailRead:
        try:
            return await self.get_registry_slot_detail(
                entry,
                payload,
                current_user=current_user,
            )
        except MeetingServiceError as exc:
            normalized_ref = entry.memo_ref_key.strip().lower()
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=str(exc),
                error_stage="unknown",
            )
        except Exception as exc:
            normalized_ref = entry.memo_ref_key.strip().lower()
            return agent_slot_detail_error(
                normalized_ref,
                slot_start=payload.slot_start,
                slot_end=payload.slot_end,
                message=f"Не удалось загрузить детали слота: {exc}",
                error_stage="unknown",
            )

