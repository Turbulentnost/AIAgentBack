from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.meeting_agent.backend import MeetingBackendError, MeetingSlot, ResolvedParticipant
from app.schemas.meeting import (
    MeetingAgentSlotApproveRequest,
    MeetingAgentSlotPreviewRequest,
    MeetingAttendeeRead,
)
from app.services.meeting_service import MeetingService, MeetingServiceError


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
        return_value=[MeetingSlot(start="2026-06-19 11:00", end="2026-06-19 11:20", confidence=0.95)]
    )
    service._backend = lambda: backend

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail",
        AsyncMock(return_value=(detail, None, True)),
    ):
        result = await service.suggest_agent_slot(
            "37da8ed8-6b19-11f1-9825-6cb31113810e",
            MeetingAgentSlotPreviewRequest(),
            current_user=user,
        )

    assert result.slot is not None
    assert result.slot.start == "2026-06-19 11:00"
    assert result.slot_label == "19.06.2026, 11:00–11:20"
    assert len(result.attendees) == 3
    assert {item.role for item in result.attendees} == {"initiator", "manager", "participant"}
    backend.find_slots.assert_awaited_once()


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
        return_value=[MeetingSlot(start="2026-06-19 11:00", end="2026-06-19 11:20", confidence=0.95)]
    )
    service._backend = lambda: backend

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail",
        AsyncMock(return_value=(detail, None, True)),
    ):
        result = await service.suggest_agent_slot("abc", MeetingAgentSlotPreviewRequest(), current_user=user)

    assert result.slot is not None
    backend.resolve_participants.assert_not_called()
    backend.find_slots.assert_awaited_once()


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
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail",
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
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail",
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
    backend.find_slots = AsyncMock(side_effect=RuntimeError("Exchange calendar failed"))
    service._backend = lambda: backend

    with patch(
        "app.services.meeting_service.MeetingMemoCacheService.get_memo_detail",
        AsyncMock(return_value=(detail, None, True)),
    ):
        result = await service.suggest_agent_slot("abc", MeetingAgentSlotPreviewRequest(), current_user=user)

    assert result.slot is None
    assert result.error == "Exchange calendar failed"
    backend.find_slots.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_agent_slot_sends_outlook_invite_only(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

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
async def test_approve_agent_slot_works_without_memo_cache(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

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
