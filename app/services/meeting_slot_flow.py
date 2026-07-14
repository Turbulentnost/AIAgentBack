"""Точки включения логики подбора слота и переносов.

Сценарии:
1. Согласование СЗ (авто) — ближайший свободный слот или переносы
2. «Запланировать вручную» — проверка выбранного пользователем слота
3. «Перенести» в реестре — авто (п.1) или ручной (п.2)
4. Удаление участников — поиск более раннего слота
5. Добавление участников — новый общий слот или текущий + переносы у новых
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from app.models.meeting_registry import MeetingRegistryEntry
from app.models.user import User
from app.schemas.meeting import (
    MeetingAgentSlotDetailRead,
    MeetingAgentSlotDetailRequest,
    MeetingAgentSlotPreviewRead,
    MeetingAgentSlotPreviewRequest,
    MeetingRegistryEarlierSlotSuggestionRead,
    MeetingRegistryRescheduleSlotPreviewRead,
    MeetingRegistryRescheduleSlotPreviewRequest,
    MeetingSlotRescheduleRecommendationRead,
)
from app.services.meeting_mappers import participant_status_read
from app.services.meeting_registry_slot import (
    suggest_common_slots_after_add,
    suggest_earlier_slots_after_removal,
)

if TYPE_CHECKING:
    from app.services.meeting_agent_slot import MeetingAgentSlotService
    from app.services.meeting_backend import MeetingBackend


class SlotSchedulingMode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"


def reschedule_recommendations_from_participants(
    participants: list[Any],
    *,
    only_fio: set[str] | None = None,
) -> list[MeetingSlotRescheduleRecommendationRead]:
    """Рекомендации по переносу из статусов участников (blocking_events)."""
    recommendations: list[MeetingSlotRescheduleRecommendationRead] = []
    for raw in participants:
        participant = (
            raw
            if isinstance(raw, MeetingSlotRescheduleRecommendationRead)
            else participant_status_read(raw, attendees=[])
        )
        if only_fio is not None and participant.fio.casefold() not in only_fio:
            continue
        if participant.status != "busy":
            continue
        for event in participant.blocking_events:
            event_label = event.event_label or "Занято"
            recommendations.append(
                MeetingSlotRescheduleRecommendationRead(
                    participant_fio=participant.fio,
                    event_label=event_label,
                    event_time_label=event.event_time_label,
                    reschedule_hint_label=event.reschedule_hint_label,
                )
            )
    return recommendations


class MeetingSlotFlowService:
    """Маршрутизация сценариев подбора слота и переносов."""

    def __init__(self, slot_service: MeetingAgentSlotService) -> None:
        self._slot_service = slot_service

    async def suggest_slot_for_sz_coordination(
        self,
        memo_ref_key: str,
        payload: MeetingAgentSlotPreviewRequest,
        *,
        current_user: User,
        attendee_specs: list[tuple[str, str]] | None = None,
    ) -> MeetingAgentSlotPreviewRead:
        """П.1: согласование СЗ — свободный слот или переносы."""
        return await self._slot_service.suggest_agent_slot_safe(
            memo_ref_key,
            payload,
            current_user=current_user,
            attendee_specs=attendee_specs,
        )

    async def validate_manual_slot(
        self,
        memo_ref_key: str,
        payload: MeetingAgentSlotDetailRequest,
        *,
        current_user: User,
    ) -> MeetingAgentSlotDetailRead:
        """П.2: «Запланировать вручную» — проверка выбранного слота."""
        return await self._slot_service.get_agent_slot_detail_safe(
            memo_ref_key,
            payload,
            current_user=current_user,
        )

    async def preview_registry_reschedule(
        self,
        entry: MeetingRegistryEntry,
        payload: MeetingRegistryRescheduleSlotPreviewRequest,
        *,
        current_user: User,
        attendee_specs: list[tuple[str, str]],
        search_after: str,
    ) -> MeetingRegistryRescheduleSlotPreviewRead:
        """П.3: «Перенести» в реестре — авто (п.1) или ручной (п.2)."""
        from app.schemas.meeting import MeetingRegistryStageRead
        from app.services.meeting_slot import format_slot_label

        normalized_ref = entry.memo_ref_key.strip().lower()
        previous_start = entry.slot_start.isoformat() if entry.slot_start else None
        previous_end = entry.slot_end.isoformat() if entry.slot_end else None
        previous_label = (
            format_slot_label(previous_start, previous_end)
            if previous_start and previous_end
            else None
        )

        slot_detail: MeetingAgentSlotDetailRead | None = None
        slot_preview: MeetingAgentSlotPreviewRead | None = None

        if payload.mode == SlotSchedulingMode.MANUAL.value:
            if not payload.slot_start or not payload.slot_end:
                raise ValueError(
                    "Для ручного переноса укажите slot_start и slot_end"
                )
            slot_detail = await self._slot_service.get_registry_slot_detail_safe(
                entry,
                MeetingAgentSlotDetailRequest(
                    slot_start=payload.slot_start,
                    slot_end=payload.slot_end,
                    duration_minutes=payload.duration_minutes,
                ),
                current_user=current_user,
            )
        else:
            slot_preview = await self._slot_service.suggest_agent_slot_safe(
                normalized_ref,
                MeetingAgentSlotPreviewRequest(
                    duration_minutes=payload.duration_minutes,
                    planned_start=search_after,
                    search_start=search_after,
                ),
                current_user=current_user,
                attendee_specs=attendee_specs,
            )

        return MeetingRegistryRescheduleSlotPreviewRead(
            ref_key=normalized_ref,
            stage=MeetingRegistryStageRead(entry.stage.value),
            previous_slot_start=previous_start,
            previous_slot_end=previous_end,
            previous_slot_label=previous_label,
            search_after=search_after,
            mode=payload.mode,
            slot_preview=slot_preview,
            slot_detail=slot_detail,
        )

    async def suggest_slot_after_participant_removal(
        self,
        *,
        entry: MeetingRegistryEntry,
        remaining_attendee_emails: list[str],
        memo_detail: dict | None,
        current_user: User,
        backend: MeetingBackend,
    ) -> MeetingRegistryEarlierSlotSuggestionRead | None:
        """П.4: после удаления — более ранний слот или оставить текущий."""
        return await suggest_earlier_slots_after_removal(
            entry=entry,
            remaining_attendee_emails=remaining_attendee_emails,
            memo_detail=memo_detail,
            current_user=current_user,
            backend=backend,
        )

    async def suggest_slot_after_participant_add(
        self,
        *,
        entry: MeetingRegistryEntry,
        attendee_emails: list[str],
        memo_detail: dict | None,
        current_user: User,
        backend: MeetingBackend,
    ) -> MeetingRegistryEarlierSlotSuggestionRead | None:
        """П.5: после добавления — новый общий слот."""
        return await suggest_common_slots_after_add(
            entry=entry,
            attendee_emails=attendee_emails,
            memo_detail=memo_detail,
            current_user=current_user,
            backend=backend,
        )

    @staticmethod
    def build_add_reschedule_recommendations(
        *,
        availability_participants: list[Any],
        added_fio: list[str],
    ) -> list[MeetingSlotRescheduleRecommendationRead]:
        """П.5: переносы только у новых участников на текущем слоте."""
        added_keys = {name.casefold() for name in added_fio}
        return reschedule_recommendations_from_participants(
            availability_participants,
            only_fio=added_keys,
        )
