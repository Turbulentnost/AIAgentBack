from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.agents.meeting_agent.backend import ResolvedParticipant
from app.models.enums import MeetingRegistryEventType, MeetingRegistryStage
from app.models.meeting_registry import MeetingRegistryEntry
from app.models.user import User
from app.schemas.meeting import (
    MeetingRegistryCurrentSlotAvailabilityRead,
    MeetingRegistryEarlierSlotCandidateRead,
    MeetingRegistryEarlierSlotSuggestionRead,
    MeetingRegistryParticipantsAddConfirmRequest,
    MeetingRegistryParticipantsApplyRequest,
    MeetingRegistryParticipantsRemovalConfirmRequest,
    MeetingSlotBlockingEventRead,
    MeetingSlotParticipantStatusRead,
    MeetingSlotRescheduleRecommendationRead,
)
from app.services.meeting_backend import MeetingQuorumSlot
from app.services.meeting_memo_cache import MemoCacheMissError
from app.services.meeting_registry_slot import (
    ADD_CURRENT_SLOT_MESSAGE,
    COMMON_SLOT_MESSAGE,
    EARLIER_SLOT_MESSAGE,
    NO_COMMON_SLOT_MESSAGE,
    _filter_and_sort_candidates,
    _filter_fully_free_candidates,
    format_add_reschedule_failure_message,
    normalize_registry_add_slot_participants,
    suggest_earlier_slots_after_removal,
)
from app.services.meeting_service import MeetingService


@pytest.fixture
def user() -> User:
    return User(id=uuid4(), email="test@turbo-don.ru", full_name="Тестовый Пользователь")


def _entry(stage: MeetingRegistryStage) -> MeetingRegistryEntry:
    now = datetime.now(timezone.utc)
    entry = MeetingRegistryEntry(
        memo_ref_key="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        stage=stage,
        invitations_sent_at=now,
        participants_count=2,
        participants=[],
    )
    entry.id = uuid4()
    return entry


def test_normalize_registry_add_slot_participants_ignores_current_meeting_for_existing() -> None:
    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.subject = "Тестирование"
    entry.slot_start = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    entry.slot_end = datetime(2026, 7, 17, 14, 0, tzinfo=timezone.utc)

    participants = [
        {
            "fio": "Комарькова Анастасия Эдуардовна",
            "email": "a@co.ru",
            "role": "participant",
            "status": "busy",
            "blocking_events": [
                {
                    "event_subject": "Тестирование",
                    "event_start_iso": "2026-07-17T12:00:00+00:00",
                    "event_end_iso": "2026-07-17T14:00:00+00:00",
                },
                {
                    "event_subject": "Тема 1",
                    "event_start_iso": "2026-07-17T12:15:00+00:00",
                    "event_end_iso": "2026-07-17T13:15:00+00:00",
                },
            ],
        },
        {
            "fio": "Лапина Арина Антоновна",
            "email": "b@co.ru",
            "role": "participant",
            "status": "free",
            "blocking_events": [],
        },
    ]

    normalized = normalize_registry_add_slot_participants(
        entry,
        participants,
        added_fio=["Лапина Арина Антоновна"],
    )

    assert normalized[0]["status"] == "free"
    assert len(normalized[0]["blocking_events"]) == 1
    assert normalized[0]["blocking_events"][0]["event_subject"] == "Тема 1"
    assert normalized[1]["status"] == "free"


def test_format_add_reschedule_failure_message_includes_busy_and_search_window() -> None:
    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.slot_start = datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc)
    entry.slot_end = datetime(2026, 7, 17, 11, 0, tzinfo=timezone.utc)

    availability = MeetingRegistryCurrentSlotAvailabilityRead(
        slot_label="17.07.2026, 13:00–14:00",
        free_count=1,
        total_count=2,
        all_free=False,
        participants=[
            MeetingSlotParticipantStatusRead(
                fio="Иванов Иван Иванович",
                email="ivanov@turbo-don.ru",
                role="participant",
                role_label="Участник",
                status="free",
            ),
            MeetingSlotParticipantStatusRead(
                fio="Мангасарян Давид Каренович",
                email="mangasaryan@turbo-don.ru",
                role="participant",
                role_label="Участник",
                status="busy",
                blocking_events=[
                    MeetingSlotBlockingEventRead(
                        event_label="Совещание по проекту",
                        event_time_label="17.07.2026, 13:00–14:00",
                        reschedule_hint_label="17.07.2026, 15:00–16:00",
                    )
                ],
            ),
        ],
    )
    recommendations = [
        MeetingSlotRescheduleRecommendationRead(
            participant_fio="Мангасарян Давид Каренович",
            event_label="Совещание по проекту",
            event_time_label="17.07.2026, 13:00–14:00",
            reschedule_hint_label="17.07.2026, 15:00–16:00",
        )
    ]

    message = format_add_reschedule_failure_message(
        current_slot_availability=availability,
        reschedule_recommendations=recommendations,
        entry=entry,
    )

    assert NO_COMMON_SLOT_MESSAGE in message
    assert "Иванов Иван Иванович" in message
    assert "Мангасарян Давид Каренович" in message
    assert "Совещание по проекту" in message
    assert "не найдено времени, когда все участники одновременно свободны" in message


def test_filter_and_sort_candidates_keeps_only_earlier_slots() -> None:
    lower = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)
    upper = datetime(2026, 7, 14, 13, 0, tzinfo=timezone.utc)
    slots = [
        MeetingQuorumSlot(
            start="2026-07-14T12:00:00+00:00",
            end="2026-07-14T13:00:00+00:00",
            confidence=0.9,
            free_count=2,
            total_count=2,
            coverage_ratio=1.0,
            weighted_coverage_ratio=1.0,
            required_ok=True,
            verified=True,
            free_attendees=[],
            busy_attendees=[],
            conflicts=[],
            reschedule_count=0,
            easy_reschedule_count=0,
            low_movability_count=0,
            impact_score=0.0,
            busy_weight_cost=0.0,
        ),
        MeetingQuorumSlot(
            start="2026-07-14T10:00:00+00:00",
            end="2026-07-14T11:00:00+00:00",
            confidence=0.9,
            free_count=2,
            total_count=2,
            coverage_ratio=1.0,
            weighted_coverage_ratio=1.0,
            required_ok=True,
            verified=True,
            free_attendees=[],
            busy_attendees=[],
            conflicts=[],
            reschedule_count=0,
            easy_reschedule_count=0,
            low_movability_count=0,
            impact_score=0.0,
            busy_weight_cost=0.0,
        ),
        MeetingQuorumSlot(
            start="2026-07-14T14:00:00+00:00",
            end="2026-07-14T15:00:00+00:00",
            confidence=0.9,
            free_count=2,
            total_count=2,
            coverage_ratio=1.0,
            weighted_coverage_ratio=1.0,
            required_ok=True,
            verified=True,
            free_attendees=[],
            busy_attendees=[],
            conflicts=[],
            reschedule_count=0,
            easy_reschedule_count=0,
            low_movability_count=0,
            impact_score=0.0,
            busy_weight_cost=0.0,
        ),
    ]

    ranked = _filter_and_sort_candidates(slots, lower_bound=lower, upper_bound=upper)

    assert [slot.start for slot in ranked] == [
        "2026-07-14T12:00:00+00:00",
        "2026-07-14T10:00:00+00:00",
    ]


def _quorum_slot(**overrides) -> MeetingQuorumSlot:
    defaults = {
        "start": "2026-07-14T10:00:00+00:00",
        "end": "2026-07-14T11:00:00+00:00",
        "confidence": 0.9,
        "free_count": 2,
        "total_count": 2,
        "coverage_ratio": 1.0,
        "weighted_coverage_ratio": 1.0,
        "required_ok": True,
        "verified": True,
        "free_attendees": ["a@turbo-don.ru", "b@turbo-don.ru"],
        "busy_attendees": [],
        "conflicts": [],
        "reschedule_count": 0,
        "easy_reschedule_count": 0,
        "low_movability_count": 0,
        "impact_score": 0.0,
        "busy_weight_cost": 0.0,
    }
    defaults.update(overrides)
    return MeetingQuorumSlot(**defaults)


def test_filter_fully_free_candidates_excludes_partial_slots() -> None:
    slots = [
        _quorum_slot(),
        _quorum_slot(
            start="2026-07-16T09:00:00+00:00",
            end="2026-07-16T11:00:00+00:00",
            free_count=1,
            total_count=2,
            coverage_ratio=0.5,
            free_attendees=["a@turbo-don.ru"],
            busy_attendees=["b@turbo-don.ru"],
        ),
    ]

    filtered = _filter_fully_free_candidates(slots)

    assert len(filtered) == 1
    assert filtered[0].coverage_ratio == 1.0
    assert filtered[0].free_count == filtered[0].total_count


@pytest.mark.asyncio
async def test_suggest_earlier_slots_after_removal_returns_none_without_window(user) -> None:
    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    backend = MagicMock()

    result = await suggest_earlier_slots_after_removal(
        entry=entry,
        remaining_attendee_emails=["a@turbo-don.ru"],
        memo_detail=None,
        current_user=user,
        backend=backend,
    )

    assert result is None
    backend.find_quorum_slots.assert_not_called()


@pytest.mark.asyncio
async def test_apply_registry_participants_removal_only_returns_earlier_slot_suggestion(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = ["Иванов Иван Иванович", "Петров Петр Петрович"]
    entry.participants_count = 2
    entry.subject = "Тестовое совещание"
    entry.slot_start = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
    entry.slot_end = datetime(2026, 7, 14, 17, 0, tzinfo=timezone.utc)
    entry.outlook_item_id = "AQMkAD-test"
    entry.outlook_changekey = "change-key"
    entry.payload = {"attendees": ["ivanov@turbo-don.ru", "petrov@turbo-don.ru"]}
    entry.updated_at = entry.invitations_sent_at

    updated_entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    updated_entry.participants = ["Иванов Иван Иванович"]
    updated_entry.participants_count = 1
    updated_entry.slot_start = entry.slot_start
    updated_entry.slot_end = entry.slot_end
    updated_entry.updated_at = entry.invitations_sent_at

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.apply_participants_update = AsyncMock(return_value=updated_entry)
    registry.save_pending_removal = AsyncMock(return_value=entry)

    backend = MagicMock()
    backend.resolve_participants = AsyncMock(
        return_value=[
            ResolvedParticipant(
                fio="Петров Петр Петрович",
                email="petrov@turbo-don.ru",
                found=True,
            ),
        ]
    )

    suggestion = MeetingRegistryEarlierSlotSuggestionRead(
        message=EARLIER_SLOT_MESSAGE,
        current_slot_label="14.07.2026, 19:00–20:00",
        search_from="2026-07-10 08:00",
        search_until="2026-07-14 19:00",
        candidates=[
            MeetingRegistryEarlierSlotCandidateRead(
                slot_start="2026-07-14T12:00:00+00:00",
                slot_end="2026-07-14T13:00:00+00:00",
                slot_label="14.07.2026, 15:00–16:00",
                coverage_ratio=1.0,
                free_attendees_count=2,
            )
        ],
    )

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch.object(MeetingService, "_backend", return_value=backend),
        patch(
            "app.services.meeting_service.dispatch_update_meeting_attendees",
            return_value={"status": "updated", "target_id": "AQMkAD-test"},
        ) as outlook_mock,
        patch(
            "app.services.meeting_slot_flow.suggest_earlier_slots_after_removal",
            AsyncMock(return_value=suggestion),
        ),
    ):
        result = await service.apply_registry_participants(
            entry.memo_ref_key,
            MeetingRegistryParticipantsApplyRequest(
                participants=["Иванов Иван Иванович"],
                removed=["Петров Петр Петрович"],
            ),
            current_user=user,
        )

    outlook_mock.assert_not_called()
    registry.apply_participants_update.assert_not_awaited()
    registry.save_pending_removal.assert_awaited_once()
    assert result.removed == ["Петров Петр Петрович"]
    assert result.added == []
    assert result.outlook_updated is False
    assert result.pending_confirmation is True
    assert result.earlier_slot_suggestion is not None
    assert result.earlier_slot_suggestion.message == EARLIER_SLOT_MESSAGE
    assert len(result.earlier_slot_suggestion.candidates) == 1


@pytest.mark.asyncio
async def test_apply_registry_participants_add_and_remove_skips_earlier_slot(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = ["Иванов Иван Иванович", "Петров Петр Петрович"]
    entry.participants_count = 2
    entry.subject = "Тестовое совещание"
    entry.slot_start = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
    entry.outlook_item_id = "AQMkAD-test"
    entry.payload = {"attendees": ["ivanov@turbo-don.ru", "petrov@turbo-don.ru"]}
    entry.updated_at = entry.invitations_sent_at

    updated_entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    updated_entry.participants = ["Иванов Иван Иванович", "Сидоров Сидор Сидорович"]
    updated_entry.participants_count = 2
    updated_entry.updated_at = entry.invitations_sent_at

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.apply_participants_update = AsyncMock(return_value=updated_entry)

    backend = MagicMock()
    backend.resolve_participants = AsyncMock(
        return_value=[
            ResolvedParticipant(fio="Сидоров Сидор Сидорович", email="sidorov@turbo-don.ru", found=True),
            ResolvedParticipant(fio="Петров Петр Петрович", email="petrov@turbo-don.ru", found=True),
        ]
    )

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch.object(MeetingService, "_backend", return_value=backend),
        patch(
            "app.services.meeting_service.dispatch_update_meeting_attendees",
            return_value={"status": "updated", "target_id": "AQMkAD-test"},
        ),
        patch(
            "app.services.meeting_slot_flow.suggest_earlier_slots_after_removal",
            AsyncMock(return_value=None),
        ) as suggest_mock,
    ):
        result = await service.apply_registry_participants(
            entry.memo_ref_key,
            MeetingRegistryParticipantsApplyRequest(
                participants=["Иванов Иван Иванович", "Сидоров Сидор Сидорович"],
                added=["Сидоров Сидор Сидорович"],
                removed=["Петров Петр Петрович"],
            ),
            current_user=user,
        )

    suggest_mock.assert_not_awaited()
    assert result.earlier_slot_suggestion is None


@pytest.mark.asyncio
async def test_apply_registry_participants_add_only_defers_when_all_free(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = ["Иванов Иван Иванович"]
    entry.participants_count = 1
    entry.subject = "Тестовое совещание"
    entry.slot_start = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
    entry.slot_end = datetime(2026, 7, 14, 17, 0, tzinfo=timezone.utc)
    entry.outlook_item_id = "AQMkAD-test"
    entry.payload = {"attendees": ["ivanov@turbo-don.ru"]}
    entry.updated_at = entry.invitations_sent_at

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.save_pending_add = AsyncMock(return_value=entry)
    registry.apply_participants_update = AsyncMock()

    backend = MagicMock()
    backend.resolve_participants = AsyncMock(
        return_value=[
            ResolvedParticipant(fio="Иванов Иван Иванович", email="ivanov@turbo-don.ru", found=True),
            ResolvedParticipant(fio="Сидоров Сидор Сидорович", email="sidorov@turbo-don.ru", found=True),
        ]
    )

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch.object(MeetingService, "_backend", return_value=backend),
        patch(
            "app.services.meeting_service.MeetingMemoCacheService",
            return_value=MagicMock(
                get_memo_detail=AsyncMock(side_effect=MemoCacheMissError("miss")),
            ),
        ),
        patch(
            "app.services.meeting_service.resolve_registry_current_slot_availability",
            AsyncMock(return_value=MagicMock(all_free=True, free_count=2, total_count=2, participants=[])),
        ),
        patch(
            "app.services.meeting_service.dispatch_update_meeting_attendees",
        ) as outlook_mock,
    ):
        result = await service.apply_registry_participants(
            entry.memo_ref_key,
            MeetingRegistryParticipantsApplyRequest(
                participants=["Иванов Иван Иванович", "Сидоров Сидор Сидорович"],
                added=["Сидоров Сидор Сидорович"],
            ),
            current_user=user,
        )

    outlook_mock.assert_not_called()
    registry.save_pending_add.assert_awaited_once()
    registry.apply_participants_update.assert_not_called()
    assert result.pending_confirmation is True
    assert result.confirmation_kind == "add_current_slot"
    assert result.message == ADD_CURRENT_SLOT_MESSAGE


@pytest.mark.asyncio
async def test_apply_registry_participants_add_does_not_gal_lookup_existing_position_title(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    entry = _entry(MeetingRegistryStage.SCHEDULED)
    entry.participants = ["Директор по развитию"]
    entry.participants_count = 1
    entry.subject = "это первое тестирование на автоматическое создание темы"
    entry.slot_start = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
    entry.slot_end = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
    entry.outlook_item_id = "AQMkAD-test"
    entry.payload = {"source": "scheduled_series"}
    entry.updated_at = entry.invitations_sent_at

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.save_pending_add = AsyncMock(return_value=entry)
    registry.apply_participants_update = AsyncMock()

    backend = MagicMock()
    backend.resolve_participants = AsyncMock(
        return_value=[
            ResolvedParticipant(
                fio="Кондратюк Михаела Борисовна",
                email="kondratyuk@turbo-don.ru",
                found=True,
            ),
        ]
    )

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch.object(MeetingService, "_backend", return_value=backend),
        patch(
            "app.services.meeting_service.MeetingMemoCacheService",
            return_value=MagicMock(
                get_memo_detail=AsyncMock(side_effect=MemoCacheMissError("miss")),
            ),
        ),
        patch(
            "app.services.meeting_service.resolve_registry_participant_emails_from_outlook",
            return_value={"директор по развитию": "director@turbo-don.ru"},
        ),
        patch(
            "app.services.meeting_service.resolve_registry_current_slot_availability",
            AsyncMock(return_value=MagicMock(all_free=True, free_count=2, total_count=2, participants=[])),
        ),
    ):
        result = await service.apply_registry_participants(
            entry.memo_ref_key,
            MeetingRegistryParticipantsApplyRequest(
                participants=["Директор по развитию", "Кондратюк Михаела Борисовна"],
                added=["Кондратюк Михаела Борисовна"],
            ),
            current_user=user,
        )

    backend.resolve_participants.assert_awaited_once_with(
        ["Кондратюк Михаела Борисовна"],
        current_user=user,
    )
    assert result.pending_confirmation is True
    assert result.confirmation_kind == "add_current_slot"


@pytest.mark.asyncio
async def test_apply_registry_participants_add_only_suggests_common_slot_when_busy(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = ["Иванов Иван Иванович"]
    entry.participants_count = 1
    entry.subject = "Тестовое совещание"
    entry.slot_start = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
    entry.slot_end = datetime(2026, 7, 14, 17, 0, tzinfo=timezone.utc)
    entry.outlook_item_id = "AQMkAD-test"
    entry.payload = {"attendees": ["ivanov@turbo-don.ru"]}
    entry.updated_at = entry.invitations_sent_at

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.save_pending_add = AsyncMock(return_value=entry)
    registry.apply_participants_update = AsyncMock()

    backend = MagicMock()
    backend.resolve_participants = AsyncMock(
        return_value=[
            ResolvedParticipant(fio="Иванов Иван Иванович", email="ivanov@turbo-don.ru", found=True),
            ResolvedParticipant(fio="Сидоров Сидор Сидорович", email="sidorov@turbo-don.ru", found=True),
        ]
    )

    suggestion = MeetingRegistryEarlierSlotSuggestionRead(
        message=COMMON_SLOT_MESSAGE,
        current_slot_label="14.07.2026, 19:00–20:00",
        search_from="2026-07-14 19:00",
        search_until="2026-08-13 19:00",
        candidates=[
            MeetingRegistryEarlierSlotCandidateRead(
                slot_start="2026-07-15T10:00:00+00:00",
                slot_end="2026-07-15T11:00:00+00:00",
                slot_label="15.07.2026, 13:00–14:00",
                coverage_ratio=1.0,
                free_attendees_count=2,
            )
        ],
    )

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch.object(MeetingService, "_backend", return_value=backend),
        patch(
            "app.services.meeting_service.MeetingMemoCacheService",
            return_value=MagicMock(
                get_memo_detail=AsyncMock(side_effect=MemoCacheMissError("miss")),
            ),
        ),
        patch(
            "app.services.meeting_service.resolve_registry_current_slot_availability",
            AsyncMock(return_value=MagicMock(all_free=False, free_count=1, total_count=2, participants=[])),
        ),
        patch(
            "app.services.meeting_slot_flow.suggest_common_slots_after_add",
            AsyncMock(return_value=suggestion),
        ),
    ):
        result = await service.apply_registry_participants(
            entry.memo_ref_key,
            MeetingRegistryParticipantsApplyRequest(
                participants=["Иванов Иван Иванович", "Сидоров Сидор Сидорович"],
                added=["Сидоров Сидор Сидорович"],
            ),
            current_user=user,
        )

    assert result.pending_confirmation is True
    assert result.confirmation_kind == "add_reschedule"
    assert result.common_slot_suggestion is not None
    assert result.common_slot_suggestion.message == COMMON_SLOT_MESSAGE


@pytest.mark.asyncio
async def test_confirm_registry_participants_add_updates_registry_at_current_slot(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = ["Иванов Иван Иванович"]
    entry.participants_count = 1
    entry.subject = "Тестовое совещание"
    entry.slot_start = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
    entry.slot_end = datetime(2026, 7, 14, 17, 0, tzinfo=timezone.utc)
    entry.outlook_item_id = "AQMkAD-test"
    entry.outlook_changekey = "change-key"
    entry.payload = {
        "attendees": ["ivanov@turbo-don.ru"],
        "pending_add": {
            "participants": ["Иванов Иван Иванович", "Сидоров Сидор Сидорович"],
            "attendees": ["ivanov@turbo-don.ru", "sidorov@turbo-don.ru"],
            "added": ["Сидоров Сидор Сидорович"],
            "removed": [],
            "keep_current_slot": True,
        },
    }
    entry.updated_at = entry.invitations_sent_at

    updated_entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    updated_entry.participants = ["Иванов Иван Иванович", "Сидоров Сидор Сидорович"]
    updated_entry.participants_count = 2
    updated_entry.slot_start = entry.slot_start
    updated_entry.slot_end = entry.slot_end
    updated_entry.updated_at = entry.invitations_sent_at

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.apply_participants_update = AsyncMock(return_value=updated_entry)
    registry.clear_pending_add = AsyncMock(return_value=updated_entry)

    backend = MagicMock()
    backend.resolve_participants = AsyncMock(
        return_value=[
            ResolvedParticipant(fio="Сидоров Сидор Сидорович", email="sidorov@turbo-don.ru", found=True),
        ]
    )

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch.object(MeetingService, "_backend", return_value=backend),
        patch(
            "app.services.meeting_service.MeetingMemoCacheService",
            return_value=MagicMock(
                get_memo_detail=AsyncMock(side_effect=MemoCacheMissError("miss")),
            ),
        ),
        patch(
            "app.services.meeting_service.dispatch_update_meeting_attendees",
            return_value={"status": "updated", "target_id": "AQMkAD-test"},
        ) as outlook_mock,
        patch(
            "app.services.meeting_service.dispatch_reschedule_meeting",
        ) as reschedule_mock,
        patch.object(
            MeetingService,
            "_reschedule_added_participant_conflicts",
            AsyncMock(),
        ),
    ):
        result = await service.confirm_registry_participants_add(
            entry.memo_ref_key,
            MeetingRegistryParticipantsAddConfirmRequest(
                participants=["Иванов Иван Иванович", "Сидоров Сидор Сидорович"],
                added=["Сидоров Сидор Сидорович"],
            ),
            current_user=user,
        )

    outlook_mock.assert_called_once()
    reschedule_mock.assert_not_called()
    registry.apply_participants_update.assert_awaited_once()
    registry.clear_pending_add.assert_awaited_once()
    assert result.outlook_updated is True
    assert result.added == ["Сидоров Сидор Сидорович"]
    assert result.participants_count == 2


@pytest.mark.asyncio
async def test_apply_registry_participants_removal_only_without_candidates_defers_until_confirm(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = ["Иванов Иван Иванович", "Петров Петр Петрович"]
    entry.participants_count = 2
    entry.subject = "Тестовое совещание"
    entry.slot_start = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
    entry.outlook_item_id = "AQMkAD-test"
    entry.outlook_changekey = "change-key"
    entry.payload = {"attendees": ["ivanov@turbo-don.ru", "petrov@turbo-don.ru"]}
    entry.updated_at = entry.invitations_sent_at

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.apply_participants_update = AsyncMock()
    registry.save_pending_removal = AsyncMock(return_value=entry)

    backend = MagicMock()
    backend.resolve_participants = AsyncMock(
        return_value=[
            ResolvedParticipant(
                fio="Петров Петр Петрович",
                email="petrov@turbo-don.ru",
                found=True,
            ),
        ]
    )

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch.object(MeetingService, "_backend", return_value=backend),
        patch(
            "app.services.meeting_service.dispatch_update_meeting_attendees",
            return_value={"status": "updated", "target_id": "AQMkAD-test"},
        ) as outlook_mock,
        patch(
            "app.services.meeting_slot_flow.suggest_earlier_slots_after_removal",
            AsyncMock(return_value=None),
        ),
    ):
        result = await service.apply_registry_participants(
            entry.memo_ref_key,
            MeetingRegistryParticipantsApplyRequest(
                participants=["Иванов Иван Иванович"],
                removed=["Петров Петр Петрович"],
            ),
            current_user=user,
        )

    outlook_mock.assert_not_called()
    registry.apply_participants_update.assert_not_awaited()
    registry.save_pending_removal.assert_awaited_once()
    assert result.pending_confirmation is True
    assert result.outlook_updated is False
    assert result.earlier_slot_suggestion is None
    assert result.participants_count == 1


@pytest.mark.asyncio
async def test_confirm_registry_participants_removal_updates_outlook_and_registry(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()
    service._sync_meeting_slot_to_cache = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = ["Иванов Иван Иванович", "Петров Петр Петрович"]
    entry.participants_count = 2
    entry.subject = "Тестовое совещание"
    entry.slot_start = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
    entry.slot_end = datetime(2026, 7, 14, 17, 0, tzinfo=timezone.utc)
    entry.outlook_item_id = "AQMkAD-test"
    entry.outlook_changekey = "change-key"
    entry.payload = {"attendees": ["ivanov@turbo-don.ru", "petrov@turbo-don.ru"]}
    entry.updated_at = entry.invitations_sent_at

    updated_entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    updated_entry.participants = ["Иванов Иван Иванович"]
    updated_entry.participants_count = 1
    updated_entry.slot_start = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    updated_entry.slot_end = datetime(2026, 7, 14, 13, 0, tzinfo=timezone.utc)
    updated_entry.updated_at = entry.invitations_sent_at

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.apply_participants_update = AsyncMock(return_value=updated_entry)
    registry.apply_reschedule = AsyncMock(return_value=updated_entry)
    registry.clear_pending_removal = AsyncMock(return_value=updated_entry)

    backend = MagicMock()
    backend.resolve_participants = AsyncMock(
        return_value=[
            ResolvedParticipant(
                fio="Петров Петр Петрович",
                email="petrov@turbo-don.ru",
                found=True,
            ),
        ]
    )

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch.object(MeetingService, "_backend", return_value=backend),
        patch(
            "app.services.meeting_service.dispatch_update_meeting_attendees",
            return_value={"status": "updated", "target_id": "AQMkAD-test"},
        ) as attendees_mock,
        patch(
            "app.services.meeting_service.dispatch_reschedule_meeting",
            return_value={"status": "rescheduled", "outlook_item_id": "AQMkAD-test"},
        ) as reschedule_mock,
        patch(
            "app.services.meeting_service.MeetingMemoCacheService",
        ) as memo_cache_cls,
    ):
        memo_cache_cls.return_value.get_memo_detail = AsyncMock(
            side_effect=MemoCacheMissError("miss")
        )
        result = await service.confirm_registry_participants_removal(
            entry.memo_ref_key,
            MeetingRegistryParticipantsRemovalConfirmRequest(
                participants=["Иванов Иван Иванович"],
                removed=["Петров Петр Петрович"],
                slot_start="2026-07-14T12:00:00+00:00",
                slot_end="2026-07-14T13:00:00+00:00",
            ),
            current_user=user,
        )

    attendees_mock.assert_called_once()
    reschedule_mock.assert_called_once()
    registry.apply_participants_update.assert_awaited_once()
    registry.apply_reschedule.assert_awaited_once()
    assert result.participants_count == 1
    assert result.removed == ["Петров Петр Петрович"]
    assert result.outlook_updated is True


@pytest.mark.asyncio
async def test_confirm_registry_participants_removal_skips_reschedule_when_slot_unchanged(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()
    service._sync_meeting_slot_to_cache = AsyncMock()

    slot_start = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
    slot_end = datetime(2026, 7, 14, 17, 0, tzinfo=timezone.utc)
    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = ["Иванов Иван Иванович", "Петров Петр Петрович"]
    entry.participants_count = 2
    entry.subject = "Тестовое совещание"
    entry.slot_start = slot_start
    entry.slot_end = slot_end
    entry.outlook_item_id = "AQMkAD-test"
    entry.outlook_changekey = "change-key"
    entry.payload = {"attendees": ["ivanov@turbo-don.ru", "petrov@turbo-don.ru"]}
    entry.updated_at = entry.invitations_sent_at

    updated_entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    updated_entry.participants = ["Иванов Иван Иванович"]
    updated_entry.participants_count = 1
    updated_entry.slot_start = slot_start
    updated_entry.slot_end = slot_end
    updated_entry.updated_at = entry.invitations_sent_at

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.apply_participants_update = AsyncMock(return_value=updated_entry)
    registry.apply_reschedule = AsyncMock(return_value=updated_entry)
    registry.clear_pending_removal = AsyncMock(return_value=updated_entry)

    backend = MagicMock()
    backend.resolve_participants = AsyncMock(
        return_value=[
            ResolvedParticipant(
                fio="Петров Петр Петрович",
                email="petrov@turbo-don.ru",
                found=True,
            ),
        ]
    )

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch.object(MeetingService, "_backend", return_value=backend),
        patch(
            "app.services.meeting_service.dispatch_update_meeting_attendees",
            return_value={"status": "updated", "target_id": "AQMkAD-test"},
        ) as attendees_mock,
        patch(
            "app.services.meeting_service.dispatch_reschedule_meeting",
            return_value={"status": "rescheduled", "outlook_item_id": "AQMkAD-test"},
        ) as reschedule_mock,
        patch(
            "app.services.meeting_service.MeetingMemoCacheService",
        ) as memo_cache_cls,
    ):
        memo_cache_cls.return_value.get_memo_detail = AsyncMock(
            side_effect=MemoCacheMissError("miss")
        )
        result = await service.confirm_registry_participants_removal(
            entry.memo_ref_key,
            MeetingRegistryParticipantsRemovalConfirmRequest(
                participants=["Иванов Иван Иванович"],
                removed=["Петров Петр Петрович"],
                slot_start=slot_start.isoformat(),
                slot_end=slot_end.isoformat(),
            ),
            current_user=user,
        )

    attendees_mock.assert_called_once()
    reschedule_mock.assert_not_called()
    registry.apply_participants_update.assert_awaited_once()
    registry.apply_reschedule.assert_not_awaited()
    service._sync_meeting_slot_to_cache.assert_not_awaited()
    assert result.message == "Состав участников совещания изменён"
