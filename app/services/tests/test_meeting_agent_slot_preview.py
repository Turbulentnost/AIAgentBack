from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.meeting_agent.backend import (
    MeetingBackendError,
    MeetingQuorumSlot,
    MeetingSlot,
    MeetingSlotConflict,
    ResolvedParticipant,
)
from app.services.meeting_backend import FindQuorumSlotsResult, FindSlotsResult
from app.schemas.meeting import (
    MeetingAgentSlotApproveRequest,
    MeetingAgentSlotDetailRequest,
    MeetingAgentSlotPreviewRequest,
    MeetingAttendeeRead,
)
from app.services.meeting_agent_errors import is_personal_calendar_access_error
from app.services.meeting_agent_slot import MeetingAgentSlotService
from app.services.meeting_exceptions import MeetingServiceError
from app.services.meeting_service import MeetingService


def _find_slots_result(
    slots: list[MeetingSlot] | None = None,
    *,
    with_snapshot: bool = False,
) -> FindSlotsResult:
    payload_slots = slots or []
    snapshot = None
    if with_snapshot and payload_slots:
        snapshot = {
            "memo_ref_key": "abc",
            "attendee_emails": ["a@turbo-don.ru", "b@turbo-don.ru", "c@turbo-don.ru"],
            "window_start": "2026-06-19T08:00:00+03:00",
            "window_end": "2026-07-19T18:00:00+03:00",
            "busy_by_attendee": {
                "a@turbo-don.ru": [
                    {
                        "start": "2026-06-20T10:00:00+03:00",
                        "end": "2026-06-20T12:00:00+03:00",
                    }
                ]
            },
        }
    return FindSlotsResult(slots=payload_slots, availability_snapshot=snapshot)


def _find_quorum_slots_result(
    slots: list[MeetingQuorumSlot] | None = None,
    *,
    with_snapshot: bool = False,
) -> FindQuorumSlotsResult:
    payload_slots = slots or []
    snapshot = None
    if with_snapshot:
        snapshot = {
            "memo_ref_key": "",
            "attendee_emails": ["a@turbo-don.ru", "b@turbo-don.ru", "c@turbo-don.ru"],
            "window_start": "2026-06-19T08:00:00+03:00",
            "window_end": "2026-07-19T18:00:00+03:00",
            "busy_by_attendee": {
                "c@turbo-don.ru": [
                    {
                        "start": "2026-07-16T16:00:00+03:00",
                        "end": "2026-07-16T17:00:00+03:00",
                    }
                ]
            },
        }
    return FindQuorumSlotsResult(slots=payload_slots, availability_snapshot=snapshot)

@pytest.fixture
def user():
    return SimpleNamespace(id=uuid.uuid4(), is_superuser=False)


def _preview_attendees() -> list[MeetingAttendeeRead]:
    return [
        MeetingAttendeeRead(
            fio="Соломичева Светлана Викторовна",
            email="a@turbo-don.ru",
            role="initiator",
            role_label="Инициатор",
            found=True,
        ),
        MeetingAttendeeRead(
            fio="Кондратюк Михаела Борисовна",
            email="b@turbo-don.ru",
            role="participant",
            role_label="Участник",
            found=True,
        ),
        MeetingAttendeeRead(
            fio="Комарькова Анастасия Эдуардовна",
            email="c@turbo-don.ru",
            role="participant",
            role_label="Участник",
            found=True,
        ),
    ]


def _quorum_slot(**overrides) -> MeetingQuorumSlot:
    payload = {
        "start": "2026-06-19 11:00",
        "end": "2026-06-19 11:20",
        "confidence": 0.9,
        "free_count": 3,
        "total_count": 3,
        "coverage_ratio": 1.0,
        "weighted_coverage_ratio": 1.0,
        "required_ok": True,
        "conflicts": [],
        "free_attendees": ["a@turbo-don.ru", "b@turbo-don.ru", "c@turbo-don.ru"],
        "busy_attendees": [],
        "verified": True,
    }
    payload.update(overrides)
    return MeetingQuorumSlot(**payload)


@pytest.mark.asyncio
async def test_suggest_agent_slot_returns_nearest_slot(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    detail = {
        "ref_key": "37da8ed8-6b19-11f1-9825-6cb31113810e",
        "queue": {"desired_meeting_date": "2026-06-19T00:00:00"},
        "application": {
            "initiator": {"full_name": "Сысоева Ирина Леонидовна"},
            "manager": {"full_name": "Иванов Иван Иванович"},
            "participants": [{"full_name": "Петров Петр Петрович"}],
            "meeting_start": "2026-06-19T11:00:00",
            "duration_minutes": 20,
        },
    }
    backend = AsyncMock()
    backend.resolve_participants = AsyncMock(
        return_value=[
            ResolvedParticipant(fio="Сысоева Ирина Леонидовна", email="irasy@turbo-don.ru", found=True),
            ResolvedParticipant(fio="Иванов Иван Иванович", email="ivanov@turbo-don.ru", found=True),
            ResolvedParticipant(fio="Петров Петр Петрович", email="petrov@turbo-don.ru", found=True),
        ]
    )
    backend.find_slots = AsyncMock(
        return_value=_find_slots_result(
            [
                MeetingSlot(
                    start="2026-06-19T11:00:00+03:00",
                    end="2026-06-19T11:20:00+03:00",
                    confidence=0.95,
                )
            ]
        )
    )
    backend.find_quorum_slots = AsyncMock()
    service._backend = lambda: backend

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail_for_agent",
        AsyncMock(return_value=(detail, None, True)),
    ):
        result = await service.suggest_agent_slot(
            "37da8ed8-6b19-11f1-9825-6cb31113810e",
            MeetingAgentSlotPreviewRequest(),
            current_user=user,
        )

    assert result.slot is not None
    assert result.slot.start == "2026-06-19T11:00:00+03:00"
    assert result.slot.end == "2026-06-19T11:20:00+03:00"
    assert result.slot_label == "19.06.2026, 11:00–11:20"
    assert result.search_mode == "all"
    assert result.coverage is not None
    assert result.coverage.ratio == 1.0
    assert result.preview_note is None
    assert len(result.slot_candidates) == 0
    assert len(result.attendees) == 3
    assert {item.role for item in result.attendees} == {"initiator", "manager", "participant"}
    group_calls = [
        call for call in backend.find_slots.await_args_list if len(call.kwargs["participants"]) == 3
    ]
    assert len(group_calls) == 1
    backend.find_quorum_slots.assert_not_called()


def test_is_personal_calendar_access_error_detects_delegate_failure() -> None:
    exc = MeetingBackendError(
        "Не удалось прочитать календарь user@turbo-don.ru: "
        "No usable default <class 'exchangelib.folders.known_folders.Calendar'> folders"
    )
    assert is_personal_calendar_access_error(exc) is True
    assert is_personal_calendar_access_error(MeetingBackendError("Свободный слот не найден")) is False


@pytest.mark.asyncio
async def test_enrich_attendees_uses_freebusy_for_nearest_slot(user) -> None:
    service = MeetingAgentSlotService(AsyncMock())
    attendees = [
        MeetingAttendeeRead(
            fio="Komarkova",
            email="user@turbo-don.ru",
            found=True,
            role="participant",
            role_label="Participant",
        )
    ]

    with patch(
        "app.services.meeting_agent_slot._find_attendee_nearest_slot",
        AsyncMock(
            return_value=MeetingSlot(
                start="2026-07-28T11:00:00+03:00",
                end="2026-07-28T12:00:00+03:00",
                confidence=0.7,
            )
        ),
    ) as find_slot:
        result = await service._enrich_attendees_with_nearest_slots(
            attendees,
            backend=AsyncMock(),
            memo=None,
            search_start="2026-07-17T09:00:00+03:00",
            duration_minutes=60,
            current_user=user,
        )

    assert result[0].nearest_slot_start == "2026-07-28T11:00:00+03:00"
    assert result[0].nearest_slot_label == "28.07.2026, 11:00–12:00"
    find_slot.assert_awaited_once()


@pytest.mark.asyncio
async def test_suggest_agent_slot_returns_availability_cache_id(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    detail = {
        "ref_key": "abc",
        "queue": {},
        "application": {
            "initiator": {"full_name": "A", "email": "a@turbo-don.ru"},
            "manager": {"full_name": "B", "email": "b@turbo-don.ru"},
            "participants": [{"full_name": "C", "email": "c@turbo-don.ru"}],
            "meeting_start": "2026-06-19T11:00:00+03:00",
            "duration_minutes": 20,
        },
    }
    backend = AsyncMock()
    backend.resolve_participants = AsyncMock(
        return_value=[
            ResolvedParticipant(fio="A", email="a@turbo-don.ru", found=True),
            ResolvedParticipant(fio="B", email="b@turbo-don.ru", found=True),
            ResolvedParticipant(fio="C", email="c@turbo-don.ru", found=True),
        ]
    )
    backend.find_slots = AsyncMock(
        return_value=_find_slots_result(
            [
                MeetingSlot(
                    start="2026-06-19T11:00:00+03:00",
                    end="2026-06-19T11:20:00+03:00",
                    confidence=0.95,
                )
            ],
            with_snapshot=True,
        )
    )
    backend.find_quorum_slots = AsyncMock()
    service._backend = lambda: backend

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail_for_agent",
        AsyncMock(return_value=(detail, None, True)),
    ):
        result = await service.suggest_agent_slot(
            "abc",
            MeetingAgentSlotPreviewRequest(),
            current_user=user,
        )

    assert result.availability_cache_id is not None


@pytest.mark.asyncio
async def test_suggest_agent_slot_uses_cached_emails_without_onec_lookup(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    detail = {
        "ref_key": "abc",
        "queue": {},
        "application": {
            "initiator": {"full_name": "A", "email": "a@turbo-don.ru"},
            "manager": {"full_name": "B", "email": "b@turbo-don.ru"},
            "participants": [{"full_name": "C", "email": "c@turbo-don.ru"}],
            "duration_minutes": 20,
        },
    }
    backend = AsyncMock()
    backend.resolve_participants = AsyncMock()
    backend.find_slots = AsyncMock(
        return_value=_find_slots_result(
            [
                MeetingSlot(
                    start="2026-06-19T11:00:00+03:00",
                    end="2026-06-19T11:20:00+03:00",
                    confidence=0.95,
                )
            ]
        )
    )
    backend.find_quorum_slots = AsyncMock()
    service._backend = lambda: backend

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail_for_agent",
        AsyncMock(return_value=(detail, None, True)),
    ):
        result = await service.suggest_agent_slot("abc", MeetingAgentSlotPreviewRequest(), current_user=user)

    assert result.slot is not None
    backend.resolve_participants.assert_not_called()
    backend.find_slots.assert_awaited_once()
    backend.find_quorum_slots.assert_not_called()


@pytest.mark.asyncio
async def test_suggest_agent_slot_reports_resolve_errors(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    detail = {
        "ref_key": "abc",
        "queue": {},
        "application": {
            "initiator": {"full_name": "A"},
            "participants": [],
        },
    }
    backend = AsyncMock()
    backend.resolve_participants = AsyncMock(
        side_effect=MeetingBackendError("Не удалось найти e-mail участников: Exchange timeout")
    )
    service._backend = lambda: backend

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail_for_agent",
        AsyncMock(return_value=(detail, None, True)),
    ):
        result = await service.suggest_agent_slot("abc", MeetingAgentSlotPreviewRequest(), current_user=user)

    assert result.slot is None
    assert "e-mail" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_suggest_agent_slot_reports_missing_emails(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    detail = {
        "ref_key": "abc",
        "queue": {},
        "application": {
            "initiator": {"full_name": "A"},
            "participants": [],
        },
    }
    backend = AsyncMock()
    backend.resolve_participants = AsyncMock(
        return_value=[ResolvedParticipant(fio="A", email=None, found=False)]
    )
    service._backend = lambda: backend

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail_for_agent",
        AsyncMock(return_value=(detail, None, True)),
    ):
        result = await service.suggest_agent_slot("abc", MeetingAgentSlotPreviewRequest(), current_user=user)

    assert result.slot is None
    assert result.error
    assert "A" in result.missing_emails


@pytest.mark.asyncio
async def test_suggest_agent_slot_reports_unexpected_calendar_errors(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    detail = {
        "ref_key": "abc",
        "queue": {},
        "application": {
            "initiator": {"full_name": "A", "email": "a@turbo-don.ru"},
            "participants": [],
            "duration_minutes": 20,
        },
    }
    backend = AsyncMock()
    backend.resolve_participants = AsyncMock()
    backend.find_slots = AsyncMock(side_effect=MeetingBackendError("Свободный слот не найден"))
    backend.find_quorum_slots = AsyncMock(side_effect=RuntimeError("Exchange calendar failed"))
    service._backend = lambda: backend

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail_for_agent",
        AsyncMock(return_value=(detail, None, True)),
    ):
        result = await service.suggest_agent_slot("abc", MeetingAgentSlotPreviewRequest(), current_user=user)

    assert result.slot is None
    assert "Exchange calendar failed" in (result.error or "")
    backend.find_slots.assert_awaited_once()
    backend.find_quorum_slots.assert_awaited_once()


@pytest.mark.asyncio
async def test_suggest_agent_slot_partial_when_all_free_missing(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    detail = {
        "ref_key": "abc",
        "queue": {},
        "application": {
            "initiator": {"full_name": "A", "email": "a@turbo-don.ru"},
            "manager": {"full_name": "B", "email": "b@turbo-don.ru"},
            "participants": [{"full_name": "C", "email": "c@turbo-don.ru"}],
            "duration_minutes": 20,
        },
    }
    backend = AsyncMock()
    backend.resolve_participants = AsyncMock()
    backend.find_slots = AsyncMock(side_effect=MeetingBackendError("Свободный слот не найден"))
    backend.find_quorum_slots = AsyncMock(
        return_value=_find_quorum_slots_result(
            [
            _quorum_slot(
                free_count=2,
                total_count=3,
                coverage_ratio=2 / 3,
                weighted_coverage_ratio=2 / 3,
                conflicts=[
                    MeetingSlotConflict(
                        email="c@turbo-don.ru",
                        fio="C",
                        role="participant",
                    )
                ],
                busy_attendees=["c@turbo-don.ru"],
                free_attendees=["a@turbo-don.ru", "b@turbo-don.ru"],
            )
            ],
            with_snapshot=True,
        )
    )
    service._backend = lambda: backend

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail_for_agent",
        AsyncMock(return_value=(detail, None, True)),
    ):
        result = await service.suggest_agent_slot("abc", MeetingAgentSlotPreviewRequest(), current_user=user)

    assert result.slot is None
    assert result.search_mode == "partial"
    assert result.preview_note
    assert len(result.slot_candidates) == 1
    assert len(result.conflicts) == 1
    assert result.availability_cache_id is not None
    backend.find_slots.assert_awaited_once()
    backend.find_quorum_slots.assert_awaited_once()


@pytest.mark.asyncio
async def test_suggest_agent_slot_all_free_via_quorum_when_find_slots_empty(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    detail = {
        "ref_key": "abc",
        "queue": {},
        "application": {
            "initiator": {"full_name": "A", "email": "a@turbo-don.ru"},
            "manager": {"full_name": "B", "email": "b@turbo-don.ru"},
            "participants": [{"full_name": "C", "email": "c@turbo-don.ru"}],
            "duration_minutes": 60,
        },
    }
    backend = AsyncMock()
    backend.resolve_participants = AsyncMock()
    backend.find_slots = AsyncMock(return_value=_find_slots_result([]))
    backend.find_quorum_slots = AsyncMock(
        return_value=_find_quorum_slots_result(
            [
            _quorum_slot(
                start="2026-07-09 11:00",
                end="2026-07-09 12:00",
                free_count=3,
                total_count=3,
                coverage_ratio=1.0,
                weighted_coverage_ratio=1.0,
                conflicts=[],
                free_attendees=["a@turbo-don.ru", "b@turbo-don.ru", "c@turbo-don.ru"],
                busy_attendees=[],
            )
            ],
            with_snapshot=True,
        )
    )
    service._backend = lambda: backend

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail_for_agent",
        AsyncMock(return_value=(detail, None, True)),
    ):
        result = await service.suggest_agent_slot("abc", MeetingAgentSlotPreviewRequest(), current_user=user)

    assert result.search_mode == "all"
    assert result.slot is not None
    assert result.slot.start == "2026-07-09 11:00"
    assert result.preview_note is None
    assert result.error is None
    assert not result.conflicts
    backend.find_slots.assert_awaited_once()
    backend.find_quorum_slots.assert_awaited_once()


@pytest.mark.asyncio
async def test_suggest_agent_slot_no_slot_returns_company_calendar_conflicts(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    detail = {
        "ref_key": "abc",
        "queue": {},
        "application": {
            "initiator": {"full_name": "A", "email": "a@turbo-don.ru"},
            "manager": {"full_name": "B", "email": "b@turbo-don.ru"},
            "participants": [{"full_name": "C", "email": "c@turbo-don.ru"}],
            "meeting_start": "2026-06-19T11:00:00",
            "duration_minutes": 20,
        },
    }
    backend = AsyncMock()
    backend.resolve_participants = AsyncMock()
    backend.find_slots = AsyncMock(return_value=_find_slots_result([]))
    backend.find_quorum_slots = AsyncMock(return_value=_find_quorum_slots_result([]))
    backend.find_company_calendar_reschedule_candidates = AsyncMock(
        return_value=[
            MeetingSlotConflict(
                email="a@turbo-don.ru",
                fio="A",
                role="initiator",
                event_start="2026-06-19 10:00",
                event_end="2026-06-19 11:00",
                event_subject="Совещание из calendar@",
                movability="high",
                movability_reason="tentative",
                source="company_calendar",
            )
        ]
    )
    service._backend = lambda: backend

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail_for_agent",
        AsyncMock(return_value=(detail, None, True)),
    ):
        result = await service.suggest_agent_slot("abc", MeetingAgentSlotPreviewRequest(), current_user=user)

    assert result.slot is None
    assert result.error_stage == "no_slot"
    assert result.error
    assert len(result.conflicts) == 1
    assert result.conflicts[0].source == "company_calendar"
    assert result.preview_note
    backend.find_company_calendar_reschedule_candidates.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_agent_slot_sends_outlook_invite_only(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    with patch(
        "app.services.meeting_service.MeetingRegistryService.upsert_from_invite",
        AsyncMock(),
    ):
        with patch(
            "app.services.meeting_service.dispatch_meeting_invite",
            return_value={
                "status": "sent",
                "attendees": ["a@turbo-don.ru", "b@turbo-don.ru", "c@turbo-don.ru"],
            },
        ) as send_invite:
            result = await service.approve_agent_slot(
            "abc",
            MeetingAgentSlotApproveRequest(
                slot_start="2026-06-20 11:00",
                slot_end="2026-06-20 11:20",
                subject="Тестовое совещание",
                location="Кабинет 201",
                attendees=_preview_attendees(),
            ),
            current_user=user,
        )

    assert result.sent is True
    assert result.attendees == ["a@turbo-don.ru", "b@turbo-don.ru", "c@turbo-don.ru"]
    send_invite.assert_called_once()
    kwargs = send_invite.call_args.kwargs
    assert kwargs["attendees"] == ["a@turbo-don.ru", "b@turbo-don.ru", "c@turbo-don.ru"]
    assert kwargs["resources"] == []
    assert kwargs["location"] == "Кабинет 201"
    service.audit.log.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_agent_slot_adds_room_as_participant_and_resource(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    with patch(
        "app.services.meeting_service.MeetingRegistryService.upsert_from_invite",
        AsyncMock(),
    ):
        with patch(
            "app.services.meeting_service.dispatch_meeting_invite",
            return_value={"status": "sent", "attendees": ["a@turbo-don.ru"]},
        ) as send_invite:
            result = await service.approve_agent_slot(
                "abc",
                MeetingAgentSlotApproveRequest(
                    slot_start="2026-06-20 11:00",
                    slot_end="2026-06-20 11:20",
                    subject="Тестовое совещание",
                    location="Зал совещаний КБ",
                    attendees=[_preview_attendees()[0]],
                ),
                current_user=user,
            )

    assert result.sent is True
    kwargs = send_invite.call_args.kwargs
    assert kwargs["resources"] == ["konfzalkb@turbo-don.ru"]
    assert "Зал совещаний КБ <konfzalkb@turbo-don.ru>" in kwargs["body"]


@pytest.mark.asyncio
async def test_approve_agent_slot_works_without_memo_cache(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    with patch(
        "app.services.meeting_service.MeetingRegistryService.upsert_from_invite",
        AsyncMock(),
    ):
        with patch(
            "app.services.meeting_service.dispatch_meeting_invite",
            return_value={"status": "sent", "attendees": ["a@turbo-don.ru"]},
        ):
            result = await service.approve_agent_slot(
            "abc",
            MeetingAgentSlotApproveRequest(
                slot_start="2026-06-20 11:00",
                slot_end="2026-06-20 11:20",
                subject="Совещание",
                attendees=[_preview_attendees()[0]],
            ),
            current_user=user,
        )

    assert result.sent is True


@pytest.mark.asyncio
async def test_approve_agent_slot_accepts_attendee_emails(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    with patch(
        "app.services.meeting_service.MeetingRegistryService.upsert_from_invite",
        AsyncMock(),
    ):
        with patch(
            "app.services.meeting_service.dispatch_meeting_invite",
            return_value={"status": "sent", "attendees": ["a@turbo-don.ru", "b@turbo-don.ru"]},
        ):
            result = await service.approve_agent_slot(
            "abc",
            MeetingAgentSlotApproveRequest(
                slot_start="2026-06-20 11:00",
                slot_end="2026-06-20 11:20",
                subject="Совещание",
                attendee_emails=["a@turbo-don.ru", "b@turbo-don.ru"],
            ),
            current_user=user,
        )

    assert result.sent is True
    assert result.attendees == ["a@turbo-don.ru", "b@turbo-don.ru"]


@pytest.mark.asyncio
async def test_approve_agent_slot_reports_outlook_error(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    with patch(
        "app.services.meeting_service.dispatch_meeting_invite",
        side_effect=RuntimeError("EWS timeout"),
    ):
        with pytest.raises(MeetingServiceError, match="Outlook/Exchange"):
            await service.approve_agent_slot(
                "abc",
                MeetingAgentSlotApproveRequest(
                    slot_start="2026-06-20 11:00",
                    slot_end="2026-06-20 11:20",
                    subject="Совещание",
                    attendee_emails=["a@turbo-don.ru"],
                ),
                current_user=user,
            )


@pytest.mark.asyncio
async def test_get_agent_slot_detail_returns_participant_status(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    detail = {
        "ref_key": "abc",
        "queue": {},
        "application": {
            "initiator": {"full_name": "A", "email": "a@turbo-don.ru"},
            "manager": {"full_name": "B", "email": "b@turbo-don.ru"},
            "participants": [{"full_name": "C", "email": "c@turbo-don.ru"}],
            "duration_minutes": 120,
            "location": "Зал совещаний КБ",
        },
    }
    backend = AsyncMock()
    backend.resolve_participants = AsyncMock()
    service._backend = lambda: backend

    raw_details = {
        "slot_start": "2026-07-09T08:45:00+03:00",
        "slot_end": "2026-07-09T10:45:00+03:00",
        "duration_minutes": 120,
        "participants": [
            {
                "fio": "A",
                "email": "a@turbo-don.ru",
                "role": "initiator",
                "status": "free",
                "blocking_events": [],
                "calendar_access_error": None,
            },
            {
                "fio": "C",
                "email": "c@turbo-don.ru",
                "role": "participant",
                "status": "busy",
                "blocking_events": [
                    {
                        "event_start": "2026-07-09T09:00:00+03:00",
                        "event_end": "2026-07-09T10:00:00+03:00",
                        "event_subject": "Sync",
                        "busy_type": "Busy",
                        "movability": "medium",
                        "movability_reason": "busy",
                        "source": "calendar",
                        "event_attendees": [
                            "c@turbo-don.ru",
                            "d@turbo-don.ru",
                            "calendar@turbo-don.ru",
                        ],
                        "event_attendee_names": [
                            "Соломичева Светлана Викторовна",
                            "Кондратюк Михаела Борисовна",
                        ],
                        "reschedule_hint_start": None,
                        "reschedule_hint_end": None,
                    }
                ],
                "calendar_access_error": None,
            },
        ],
    }

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail_for_agent",
        AsyncMock(return_value=(detail, None, True)),
    ):
        with patch(
            "app.services.meeting_agent_slot.build_slot_participant_details",
            return_value=raw_details,
        ):
            with patch(
                "app.services.meeting_agent_slot.check_rooms_status",
                return_value=[
                    {
                        "name": "Зал совещаний КБ",
                        "email": "konfzalkb@turbo-don.ru",
                        "status": "free",
                        "status_label": "свободна",
                        "busy_events": 0,
                    }
                ],
            ):
                result = await service.get_agent_slot_detail(
                    "abc",
                    MeetingAgentSlotDetailRequest(
                        slot_start="2026-07-09 08:45",
                        slot_end="2026-07-09 10:45",
                    ),
                    current_user=user,
                )

    assert result.error is None
    assert len(result.participants) == 3
    room_participant = next(item for item in result.participants if item.role == "room")
    assert room_participant.role_label == "Переговорная"
    assert room_participant.status == "free"
    assert result.room is not None
    assert result.room.name == "Зал совещаний КБ"
    assert result.room.available is True
    assert result.room.status_label == "свободна"
    busy = next(item for item in result.participants if item.status == "busy")
    assert busy.blocking_events[0].event_subject == "Sync"
    assert busy.blocking_events[0].event_label == "Sync"
    assert busy.blocking_events[0].event_attendee_names == [
        "Соломичева С.В.",
        "Кондратюк М.Б.",
    ]


@pytest.mark.asyncio
async def test_get_agent_slot_detail_reuses_availability_cache(user) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.services.slot_availability_cache import (
        serialize_busy_snapshot,
        store_availability_snapshot,
    )

    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    detail = {
        "ref_key": "abc",
        "queue": {},
        "application": {
            "initiator": {"full_name": "A", "email": "a@turbo-don.ru"},
            "manager": {"full_name": "B", "email": "b@turbo-don.ru"},
            "participants": [{"full_name": "C", "email": "c@turbo-don.ru"}],
            "duration_minutes": 20,
            "location": "Зал совещаний КБ",
        },
    }
    backend = AsyncMock()
    backend.resolve_participants = AsyncMock()
    service._backend = lambda: backend

    tz = ZoneInfo("Europe/Moscow")
    busy_block = (
        datetime(2026, 7, 20, 10, 0, tzinfo=tz),
        datetime(2026, 7, 20, 12, 0, tzinfo=tz),
    )
    cache_id = store_availability_snapshot(
        serialize_busy_snapshot(
            memo_ref_key="abc",
            attendee_emails=["a@turbo-don.ru", "b@turbo-don.ru", "c@turbo-don.ru"],
            window_start=datetime(2026, 7, 14, 8, 0, tzinfo=tz),
            window_end=datetime(2026, 8, 13, 18, 0, tzinfo=tz),
            busy_by_attendee={"a@turbo-don.ru": [busy_block]},
        )
    )

    raw_details = {
        "slot_start": "2026-07-20T11:00:00+03:00",
        "slot_end": "2026-07-20T11:30:00+03:00",
        "duration_minutes": 30,
        "participants": [],
    }

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail_for_agent",
        AsyncMock(return_value=(detail, None, True)),
    ):
        with patch(
            "app.services.meeting_agent_slot.build_slot_participant_details",
            return_value=raw_details,
        ) as build_details:
            with patch(
                "app.services.meeting_agent_slot.check_rooms_status",
                return_value=[
                    {
                        "name": "Зал совещаний КБ",
                        "email": "konfzalkb@turbo-don.ru",
                        "status": "free",
                        "status_label": "свободна",
                        "busy_events": 0,
                    }
                ],
            ):
                result = await service.get_agent_slot_detail(
                    "abc",
                    MeetingAgentSlotDetailRequest(
                        slot_start="2026-07-20T11:00:00+03:00",
                        slot_end="2026-07-20T11:30:00+03:00",
                        availability_cache_id=cache_id,
                    ),
                    current_user=user,
                )

    assert result.error is None
    assert build_details.call_count == 1
    assert build_details.call_args.kwargs["cached_busy_by_attendee"] is not None
    assert build_details.call_args.kwargs["cached_busy_by_attendee"]["a@turbo-don.ru"] == [
        busy_block
    ]


@pytest.mark.asyncio
async def test_get_agent_slot_detail_uses_company_calendar_when_slot_busy(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    detail = {
        "ref_key": "abc",
        "queue": {},
        "application": {
            "initiator": {"full_name": "A", "email": "a@turbo-don.ru"},
            "manager": {"full_name": "B", "email": "b@turbo-don.ru"},
            "participants": [{"full_name": "C", "email": "c@turbo-don.ru"}],
            "duration_minutes": 120,
            "location": "Зал совещаний КБ",
        },
    }
    backend = AsyncMock()
    backend.resolve_participants = AsyncMock()
    backend.find_company_calendar_reschedule_candidates = AsyncMock(
        return_value=[
            MeetingSlotConflict(
                fio="C",
                email="c@turbo-don.ru",
                event_subject="Корпоративная встреча",
                event_start="2026-07-09T09:00:00+03:00",
                event_end="2026-07-09T10:00:00+03:00",
            )
        ]
    )
    service._backend = lambda: backend

    all_free_details = {
        "slot_start": "2026-07-09T08:45:00+03:00",
        "slot_end": "2026-07-09T10:45:00+03:00",
        "duration_minutes": 120,
        "participants": [
            {
                "fio": "A",
                "email": "a@turbo-don.ru",
                "role": "initiator",
                "status": "free",
                "blocking_events": [],
                "calendar_access_error": None,
            },
            {
                "fio": "C",
                "email": "c@turbo-don.ru",
                "role": "participant",
                "status": "free",
                "blocking_events": [],
                "calendar_access_error": None,
            },
        ],
    }
    busy_details = {
        **all_free_details,
        "participants": [
            all_free_details["participants"][0],
            {
                **all_free_details["participants"][1],
                "status": "busy",
                "blocking_events": [
                    {
                        "event_start": "2026-07-09T09:00:00+03:00",
                        "event_end": "2026-07-09T10:00:00+03:00",
                        "event_subject": "Sync",
                        "busy_type": "Busy",
                        "movability": "medium",
                        "movability_reason": "busy",
                        "source": "calendar",
                        "event_attendees": ["c@turbo-don.ru"],
                        "event_attendee_names": [],
                        "reschedule_hint_start": None,
                        "reschedule_hint_end": None,
                    }
                ],
            },
        ],
    }

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail_for_agent",
        AsyncMock(return_value=(detail, None, True)),
    ):
        with patch(
            "app.services.meeting_agent_slot.build_slot_participant_details",
            return_value=all_free_details,
        ):
            with patch(
                "app.services.meeting_agent_slot.check_rooms_status",
                return_value=[
                    {
                        "name": "Зал совещаний КБ",
                        "email": "konfzalkb@turbo-don.ru",
                        "status": "free",
                        "status_label": "свободна",
                        "busy_events": 0,
                    }
                ],
            ):
                free_result = await service.get_agent_slot_detail(
                    "abc",
                    MeetingAgentSlotDetailRequest(
                        slot_start="2026-07-09 08:45",
                        slot_end="2026-07-09 10:45",
                    ),
                    current_user=user,
                )

    assert free_result.slot_available is True
    backend.find_company_calendar_reschedule_candidates.assert_not_awaited()

    backend.find_company_calendar_reschedule_candidates.reset_mock()
    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail_for_agent",
        AsyncMock(return_value=(detail, None, True)),
    ):
        with patch(
            "app.services.meeting_agent_slot.build_slot_participant_details",
            return_value=busy_details,
        ):
            with patch(
                "app.services.meeting_agent_slot.check_rooms_status",
                return_value=[
                    {
                        "name": "Зал совещаний КБ",
                        "email": "konfzalkb@turbo-don.ru",
                        "status": "free",
                        "status_label": "свободна",
                        "busy_events": 0,
                    }
                ],
            ):
                busy_result = await service.get_agent_slot_detail(
                    "abc",
                    MeetingAgentSlotDetailRequest(
                        slot_start="2026-07-09 08:45",
                        slot_end="2026-07-09 10:45",
                    ),
                    current_user=user,
                )

    assert busy_result.slot_available is False
    backend.find_company_calendar_reschedule_candidates.assert_not_awaited()
    labels = [item.event_label for item in busy_result.reschedule_recommendations]
    assert "Sync" in labels


@pytest.mark.asyncio
async def test_get_agent_slot_detail_skips_company_calendar_when_only_room_busy(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    detail = {
        "ref_key": "abc",
        "queue": {},
        "application": {
            "initiator": {"full_name": "A", "email": "a@turbo-don.ru"},
            "manager": {"full_name": "B", "email": "b@turbo-don.ru"},
            "participants": [{"full_name": "C", "email": "c@turbo-don.ru"}],
            "duration_minutes": 120,
            "location": "Зал совещаний КБ",
        },
    }
    backend = AsyncMock()
    backend.resolve_participants = AsyncMock()
    backend.find_company_calendar_reschedule_candidates = AsyncMock(return_value=[])
    service._backend = lambda: backend

    all_free_details = {
        "slot_start": "2026-07-09T08:45:00+03:00",
        "slot_end": "2026-07-09T10:45:00+03:00",
        "duration_minutes": 120,
        "participants": [
            {
                "fio": "A",
                "email": "a@turbo-don.ru",
                "role": "initiator",
                "status": "free",
                "blocking_events": [],
                "calendar_access_error": None,
            },
            {
                "fio": "C",
                "email": "c@turbo-don.ru",
                "role": "participant",
                "status": "free",
                "blocking_events": [],
                "calendar_access_error": None,
            },
        ],
    }

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail_for_agent",
        AsyncMock(return_value=(detail, None, True)),
    ):
        with patch(
            "app.services.meeting_agent_slot.build_slot_participant_details",
            return_value=all_free_details,
        ):
            with patch(
                "app.services.meeting_agent_slot.check_rooms_status",
                return_value=[
                    {
                        "name": "Зал совещаний КБ",
                        "email": "konfzalkb@turbo-don.ru",
                        "status": "busy",
                        "status_label": "занята",
                        "busy_events": 1,
                    }
                ],
            ):
                result = await service.get_agent_slot_detail(
                    "abc",
                    MeetingAgentSlotDetailRequest(
                        slot_start="2026-07-09 08:45",
                        slot_end="2026-07-09 10:45",
                    ),
                    current_user=user,
                )

    assert result.error is None
    assert result.slot_available is False
    backend.find_company_calendar_reschedule_candidates.assert_not_awaited()
    labels = [item.event_label for item in result.reschedule_recommendations]
    assert "Переговорная занята" in labels


def test_format_fio_short_uses_surname_and_initials() -> None:
    from app.services.meeting_mappers import format_fio_short

    assert format_fio_short("Соломичева Светлана Викторовна") == "Соломичева С.В."
    assert format_fio_short("Петров Петр") == "Петров П."


def test_event_label_without_subject_is_zanyat() -> None:
    from app.services.meeting_mappers import conflict_read, event_label_for_record
    from app.agents.meeting_agent.backend import MeetingSlotConflict

    assert event_label_for_record(
        event_subject=None,
        event_start="2026-07-07T13:30:00+03:00",
        event_end="2026-07-07T14:00:00+03:00",
    ) == "Занят"
    assert event_label_for_record(
        event_subject="Еженедельное совещание",
        event_start="2026-07-07T13:30:00+03:00",
        event_end="2026-07-07T14:00:00+03:00",
    ) == "Еженедельное совещание"

    conflict = conflict_read(
        MeetingSlotConflict(
            email="a@turbo-don.ru",
            event_start="2026-07-14T09:00:00+03:00",
            event_end="2026-07-14T09:30:00+03:00",
            event_subject="Sync",
        ),
        attendees=[],
    )
    assert conflict.event_start == "14.07.2026, 09:00"
    assert conflict.event_end == "09:30"
    assert conflict.event_time_label == "14.07.2026, 09:00–09:30"


def test_quorum_slot_read_includes_attendee_names() -> None:
    from app.services.meeting_mappers import quorum_slot_read

    item = _quorum_slot(
        free_attendees=["a@turbo-don.ru"],
        busy_attendees=["c@turbo-don.ru"],
        free_count=1,
        total_count=2,
        coverage_ratio=0.5,
        weighted_coverage_ratio=0.5,
    )
    attendees = [
        MeetingAttendeeRead(
            fio="Комарькова",
            email="a@turbo-don.ru",
            role="initiator",
            role_label="Инициатор",
        ),
        MeetingAttendeeRead(
            fio="Целищев",
            email="c@turbo-don.ru",
            role="participant",
            role_label="Участник",
        ),
    ]
    read = quorum_slot_read(item, attendees=attendees)
    assert read.free_attendee_names == ["Комарькова"]
    assert read.busy_attendee_names == ["Целищев"]
