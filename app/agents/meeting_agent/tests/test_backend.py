from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.agents.meeting_agent.backend import (
    MeetingBackend,
    MeetingMemo,
    ResolvedParticipant,
    _find_memo_document,
    _normalize_memo,
)


@pytest.fixture
def user():
    from app.models.user import User

    return User(id=uuid.uuid4(), email="test@turbo-don.ru", hashed_password="x")


@pytest.fixture
def sample_document() -> dict:
    return {
        "memo": {
            "Ref_Key": "11111111-1111-1111-1111-111111111111",
            "Number": "СЗ-001",
            "Date": "2026-06-10",
            "Комментарий": "Еженедельное совещание",
        },
        "participants": [{"Description": "Петров Петр Петрович"}],
    }


def test_find_memo_document_by_ref_key(sample_document: dict) -> None:
    documents = [sample_document]
    found = _find_memo_document(documents, "11111111-1111-1111-1111-111111111111", None)
    assert found is sample_document


def test_normalize_memo_extracts_participants(sample_document: dict) -> None:
    memo = _normalize_memo(sample_document)
    assert memo.number == "СЗ-001"
    assert memo.participant_fio == ["Петров Петр Петрович"]
    assert memo.subject == "Еженедельное совещание"


@pytest.mark.asyncio
async def test_load_memo_calls_get_meeting_memos(user, sample_document: dict) -> None:
    backend = MeetingBackend(db=AsyncMock())
    backend._invoke = AsyncMock(return_value={"documents": [sample_document]})  # type: ignore[method-assign]

    memo = await backend.load_memo(
        memo_ref_key="11111111-1111-1111-1111-111111111111",
        current_user=user,
    )

    assert memo.number == "СЗ-001"
    backend._invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_participants(user) -> None:
    backend = MeetingBackend(db=AsyncMock())
    backend._invoke = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "results": [
                {
                    "fio_query": "Петров Петр Петрович",
                    "fio": "Петров Петр Петрович",
                    "user_ref": "ref",
                    "register_published": True,
                    "emails": [{"email": "petrov@turbo-don.ru"}],
                }
            ],
            "errors": [],
        }
    )

    participants = await backend.resolve_participants(["Петров Петр Петрович"], current_user=user)

    assert len(participants) == 1
    assert participants[0].email == "petrov@turbo-don.ru"
    assert participants[0].found is True


@pytest.mark.asyncio
async def test_prepare_invite(user, sample_document: dict) -> None:
    backend = MeetingBackend(db=AsyncMock())
    memo = _normalize_memo(sample_document)
    participants = [ResolvedParticipant(fio="Петров Петр Петрович", email="petrov@turbo-don.ru", found=True)]

    invite = await backend.prepare_invite(
        memo=memo,
        participants=participants,
        selected_slot={"start": "2026-06-11 10:00", "end": "2026-06-11 11:00"},
        selected_room={"name": "Переговорная 1", "email": "room1@turbo-don.ru"},
        subject=None,
        current_user=user,
    )

    assert invite is not None
    assert invite.subject == "Еженедельное совещание"
    assert invite.attendees == ["petrov@turbo-don.ru"]
    assert invite.location == "Переговорная 1"
