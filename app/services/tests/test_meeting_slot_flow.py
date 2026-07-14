from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.meeting import (
    MeetingAgentSlotDetailRead,
    MeetingAgentSlotDetailRequest,
    MeetingAgentSlotPreviewRead,
    MeetingAgentSlotPreviewRequest,
    MeetingRegistryRescheduleSlotPreviewRequest,
    MeetingSlotRescheduleRecommendationRead,
)
from app.services.meeting_slot_flow import reschedule_recommendations_from_participants


def test_reschedule_recommendations_filters_by_added_fio() -> None:
    participants = [
        {
            "fio": "Иванов И.И.",
            "email": "ivanov@test.ru",
            "role": "participant",
            "status": "busy",
            "blocking_events": [
                {
                    "event_subject": "Совещание А",
                    "event_start": "2026-07-15 10:00",
                    "event_end": "2026-07-15 11:00",
                    "reschedule_hint_label": "11:00–12:00",
                }
            ],
        },
        {
            "fio": "Петров П.П.",
            "email": "petrov@test.ru",
            "role": "participant",
            "status": "free",
            "blocking_events": [],
        },
    ]

    result = reschedule_recommendations_from_participants(
        participants,
        only_fio={"иванов и.и."},
    )

    assert len(result) == 1
    assert result[0].participant_fio == "Иванов И.И."
    assert result[0].event_label == "Совещание А"


@pytest.mark.asyncio
async def test_preview_registry_reschedule_auto_mode() -> None:
    from app.services.meeting_slot_flow import MeetingSlotFlowService

    slot_service = MagicMock()
    slot_service.suggest_agent_slot_safe = AsyncMock(
        return_value=MeetingAgentSlotPreviewRead(
            memo_ref_key="abc",
            slot=None,
        )
    )
    flow = MeetingSlotFlowService(slot_service)
    entry = MagicMock()
    entry.memo_ref_key = "abc"
    entry.stage.value = "invitations_sent"
    entry.slot_start = None
    entry.slot_end = None

    result = await flow.preview_registry_reschedule(
        entry,
        MeetingRegistryRescheduleSlotPreviewRequest(mode="auto"),
        current_user=MagicMock(),
        attendee_specs=[("Иванов", "participant")],
        search_after="2026-07-15 10:00",
    )

    assert result.mode == "auto"
    assert result.slot_preview is not None
    assert result.slot_detail is None
    slot_service.suggest_agent_slot_safe.assert_awaited_once()


@pytest.mark.asyncio
async def test_preview_registry_reschedule_manual_mode() -> None:
    from app.services.meeting_slot_flow import MeetingSlotFlowService

    slot_service = MagicMock()
    slot_service.get_registry_slot_detail_safe = AsyncMock(
        return_value=MeetingAgentSlotDetailRead(
            memo_ref_key="abc",
            slot_start="2026-07-15 10:00",
            slot_end="2026-07-15 11:00",
            slot_label="15.07 10:00–11:00",
            duration_minutes=60,
            slot_available=True,
        )
    )
    flow = MeetingSlotFlowService(slot_service)
    entry = MagicMock()
    entry.memo_ref_key = "abc"
    entry.stage.value = "invitations_sent"
    entry.slot_start = None
    entry.slot_end = None

    result = await flow.preview_registry_reschedule(
        entry,
        MeetingRegistryRescheduleSlotPreviewRequest(
            mode="manual",
            slot_start="2026-07-15 10:00",
            slot_end="2026-07-15 11:00",
        ),
        current_user=MagicMock(),
        attendee_specs=[("Иванов", "participant")],
        search_after="2026-07-15 10:00",
    )

    assert result.mode == "manual"
    assert result.slot_detail is not None
    assert result.slot_detail.slot_available is True
    assert result.slot_preview is None
    slot_service.get_registry_slot_detail_safe.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_add_reschedule_recommendations() -> None:
    from app.services.meeting_slot_flow import MeetingSlotFlowService

    slot_service = MagicMock()
    flow = MeetingSlotFlowService(slot_service)
    participants = [
        {
            "fio": "Новый Участник",
            "email": "new@test.ru",
            "role": "participant",
            "status": "busy",
            "blocking_events": [
                {
                    "event_subject": "Блок",
                    "event_start": "2026-07-15 10:00",
                    "event_end": "2026-07-15 11:00",
                    "reschedule_hint_label": "12:00",
                }
            ],
        }
    ]

    result = flow.build_add_reschedule_recommendations(
        availability_participants=participants,
        added_fio=["Новый Участник"],
    )

    assert len(result) == 1
    assert isinstance(result[0], MeetingSlotRescheduleRecommendationRead)
