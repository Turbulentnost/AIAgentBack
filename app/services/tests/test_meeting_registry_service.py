from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.enums import MeetingRegistryStage
from app.models.meeting_registry import MeetingRegistryEntry
from app.models.user import User
from app.schemas.meeting import MeetingRegistryCancelRequest, MeetingRegistryStageRead
from app.services.meeting_registry_service import (
    MeetingRegistryService,
    build_stage_counts,
    stage_index,
)
from app.services.meeting_service import MeetingService


@pytest.fixture
def user() -> User:
    return User(id=uuid4(), email="test@turbo-don.ru", full_name="Тестовый Пользователь")


def _entry(stage: MeetingRegistryStage) -> MeetingRegistryEntry:
    now = datetime.now(timezone.utc)
    return MeetingRegistryEntry(
        memo_ref_key="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        stage=stage,
        invitations_sent_at=now,
        participants_count=2,
        participants=[],
    )


def test_stage_index_cancelled_is_outside_pipeline() -> None:
    assert stage_index(MeetingRegistryStage.CANCELLED) == -1
    assert stage_index(MeetingRegistryStage.INVITATIONS_SENT) == 0


def test_build_stage_counts_excludes_cancelled_from_pipeline() -> None:
    entries = [
        _entry(MeetingRegistryStage.INVITATIONS_SENT),
        _entry(MeetingRegistryStage.PROTOCOL_CREATED),
        _entry(MeetingRegistryStage.CANCELLED),
    ]
    counts = build_stage_counts(entries)
    assert counts["all"] == 3
    assert counts["approved"] == 2
    assert counts["cancelled"] == 1
    assert counts["invitations_sent"] == 2
    assert counts["protocol_created"] == 1


@pytest.mark.asyncio
async def test_cancel_registry_meeting_cancels_outlook_and_updates_stage(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.outlook_item_id = "AQMkAD-test"
    entry.outlook_changekey = "DwAA-test"
    entry.subject = "Тестовое совещание"

    registry = MagicMock()
    registry.get_entry = AsyncMock(side_effect=[entry, entry])
    registry.mark_cancelled = AsyncMock(
        return_value=MeetingRegistryEntry(
            memo_ref_key=entry.memo_ref_key,
            stage=MeetingRegistryStage.CANCELLED,
            invitations_sent_at=entry.invitations_sent_at,
            participants_count=2,
            payload={"cancelled_at": "2026-07-09T09:00:00+00:00", "outlook_cancelled": True},
        )
    )

    with patch(
        "app.services.meeting_service.MeetingRegistryService",
        return_value=registry,
    ):
        with patch(
            "app.services.meeting_service.dispatch_cancel_meeting",
            return_value={"status": "cancelled", "action": "cancel"},
        ) as cancel_outlook:
            result = await service.cancel_registry_meeting(
                entry.memo_ref_key,
                MeetingRegistryCancelRequest(message="Переносится"),
                current_user=user,
            )

    assert result.cancelled is True
    assert result.stage == MeetingRegistryStageRead.CANCELLED
    assert result.outlook_cancelled is True
    cancel_outlook.assert_called_once()
    registry.mark_cancelled.assert_awaited_once()
    service.audit.log.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_registry_meeting_is_idempotent_for_cancelled(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    entry = _entry(MeetingRegistryStage.CANCELLED)
    entry.payload = {"cancelled_at": "2026-07-09T09:00:00+00:00", "outlook_cancelled": True}

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)

    with patch(
        "app.services.meeting_service.MeetingRegistryService",
        return_value=registry,
    ):
        with patch("app.services.meeting_service.dispatch_cancel_meeting") as cancel_outlook:
            result = await service.cancel_registry_meeting(
                entry.memo_ref_key,
                MeetingRegistryCancelRequest(),
                current_user=user,
            )

    assert result.cancelled is True
    assert result.stage == MeetingRegistryStageRead.CANCELLED
    cancel_outlook.assert_not_called()
    registry.mark_cancelled.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_registry_meeting_soft_fails_when_outlook_missing(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.outlook_item_id = "missing-id"
    entry.subject = "Тестовое совещание"
    entry.slot_start = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.mark_cancelled = AsyncMock(
        return_value=MeetingRegistryEntry(
            memo_ref_key=entry.memo_ref_key,
            stage=MeetingRegistryStage.CANCELLED,
            invitations_sent_at=entry.invitations_sent_at,
            participants_count=2,
            payload={"cancelled_at": "2026-07-09T09:00:00+00:00", "outlook_cancelled": False},
        )
    )

    with patch(
        "app.services.meeting_service.MeetingRegistryService",
        return_value=registry,
    ):
        with patch(
            "app.services.meeting_service.dispatch_cancel_meeting",
            side_effect=RuntimeError("Совещание не найдено: test"),
        ):
            result = await service.cancel_registry_meeting(
                entry.memo_ref_key,
                MeetingRegistryCancelRequest(),
                current_user=user,
            )

    assert result.stage == MeetingRegistryStageRead.CANCELLED
    assert result.outlook_cancelled is False
    assert result.outlook_warning
    registry.mark_cancelled.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_registry_meeting_falls_back_to_subject_start(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.outlook_item_id = "stale-id"
    entry.outlook_changekey = "stale-ck"
    entry.subject = "Тестовое совещание"
    entry.slot_start = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.mark_cancelled = AsyncMock(
        return_value=MeetingRegistryEntry(
            memo_ref_key=entry.memo_ref_key,
            stage=MeetingRegistryStage.CANCELLED,
            invitations_sent_at=entry.invitations_sent_at,
            participants_count=2,
            payload={"cancelled_at": "2026-07-09T09:00:00+00:00", "outlook_cancelled": True},
        )
    )

    calls: list[dict] = []

    def fake_dispatch(**kwargs):
        calls.append(kwargs)
        if "item_id" in kwargs:
            raise RuntimeError("Совещание не найдено по id: stale-id")
        return {"status": "cancelled", "action": "cancel"}

    with patch(
        "app.services.meeting_service.MeetingRegistryService",
        return_value=registry,
    ):
        with patch(
            "app.services.meeting_service.dispatch_cancel_meeting",
            side_effect=fake_dispatch,
        ):
            result = await service.cancel_registry_meeting(
                entry.memo_ref_key,
                MeetingRegistryCancelRequest(message="Тест"),
                current_user=user,
            )

    assert result.stage == MeetingRegistryStageRead.CANCELLED
    assert len(calls) == 2
    assert calls[0]["item_id"] == "stale-id"
    assert calls[1]["subject"] == "Тестовое совещание"


@pytest.mark.asyncio
async def test_mark_cancelled_sets_payload(user) -> None:
    db = AsyncMock()
    db.flush = AsyncMock()
    service = MeetingRegistryService(db)

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.payload = {"attendees": ["a@turbo-don.ru"]}

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = entry
    db.execute = AsyncMock(return_value=result_mock)

    updated = await service.mark_cancelled(
        memo_ref_key=entry.memo_ref_key,
        cancelled_by=user,
        message="Отмена",
        outlook_cancelled=True,
    )

    assert updated.stage == MeetingRegistryStage.CANCELLED
    assert updated.payload["cancel_message"] == "Отмена"
    assert updated.payload["outlook_cancelled"] is True
    assert updated.payload["cancelled_by_user_id"] == str(user.id)
    assert "cancelled_at" in updated.payload


@pytest.mark.asyncio
async def test_apply_reschedule_restores_invitations_sent_stage(user) -> None:
    db = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    service = MeetingRegistryService(db)

    entry = _entry(MeetingRegistryStage.CANCELLED)
    entry.payload = {
        "attendees": ["a@turbo-don.ru"],
        "cancelled_at": "2026-07-09T10:00:00+00:00",
        "outlook_cancelled": True,
    }

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = entry
    db.execute = AsyncMock(return_value=result_mock)

    updated = await service.apply_reschedule(
        memo_ref_key=entry.memo_ref_key,
        slot_start="2026-07-15T13:00:00+03:00",
        slot_end="2026-07-15T14:00:00+03:00",
        subject="Совещание",
        location="Зал совещаний КБ",
        attendees=["a@turbo-don.ru", "b@turbo-don.ru"],
        rescheduled_by=user,
        sent_payload={"status": "rescheduled", "outlook_item_id": "new-id"},
        reschedule_message="Перенос",
    )

    assert updated.stage == MeetingRegistryStage.INVITATIONS_SENT
    assert updated.payload["attendees"] == ["a@turbo-don.ru", "b@turbo-don.ru"]
    assert updated.payload["reschedule_message"] == "Перенос"
    assert "cancelled_at" not in updated.payload
    assert updated.outlook_item_id == "new-id"


@pytest.mark.asyncio
async def test_upsert_from_invite_saves_participants_from_memo_detail(user) -> None:
    db = AsyncMock()
    db.flush = AsyncMock()
    service = MeetingRegistryService(db)

    added_entry: MeetingRegistryEntry | None = None

    def capture_add(entry: MeetingRegistryEntry) -> None:
        nonlocal added_entry
        added_entry = entry

    db.add = MagicMock(side_effect=capture_add)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result_mock)

    memo_detail = {
        "number": "000010674",
        "title": "Организация комиссионной приёмки",
        "application": {
            "initiator": {"full_name": "Мануков Роман Григорьевич"},
            "manager": {"full_name": "Мануков Роман Григорьевич"},
            "participants": [
                {"full_name": "Арсуноев Михаил Магомедович"},
                {"full_name": "Грунтовский Дмитрий Дмитриевич"},
                {"full_name": "Асланян Артур Карапетович"},
            ],
            "participants_count": 3,
        },
    }

    await service.upsert_from_invite(
        memo_ref_key="c9d6ccaa-d60c-5814-8468-7d440d393ee0",
        slot_start="2026-07-14 16:00",
        slot_end="2026-07-14 17:00",
        subject="Тема",
        location="Зал",
        attendees=["a@turbo-don.ru"],
        approved_by=user,
        memo_detail=memo_detail,
        attendee_details=[{"fio": "Только приглашённый"}],
    )

    assert added_entry is not None
    assert added_entry.participants == [
        "Арсуноев Михаил Магомедович",
        "Грунтовский Дмитрий Дмитриевич",
        "Асланян Артур Карапетович",
    ]
    assert added_entry.participants_count == 3
    assert added_entry.initiator_name == "Мануков Роман Григорьевич"


@pytest.mark.asyncio
async def test_upsert_from_invite_updates_empty_participants_on_repeat_invite(user) -> None:
    db = AsyncMock()
    db.flush = AsyncMock()
    service = MeetingRegistryService(db)

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = []
    entry.participants_count = 0

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = entry
    db.execute = AsyncMock(return_value=result_mock)

    memo_detail = {
        "application": {
            "participants": [
                {"full_name": "Петров Петр Петрович"},
                {"full_name": "Иванов Иван Иванович"},
            ],
            "participants_count": 2,
        },
    }

    updated = await service.upsert_from_invite(
        memo_ref_key=entry.memo_ref_key,
        slot_start="2026-07-15 13:00:00+03:00",
        slot_end="2026-07-15 14:00:00+03:00",
        subject="Совещание",
        location="Зал",
        attendees=["a@turbo-don.ru"],
        approved_by=user,
        memo_detail=memo_detail,
    )

    assert updated.participants == [
        "Петров Петр Петрович",
        "Иванов Иван Иванович",
    ]
    assert updated.participants_count == 2


def test_resolve_registry_participant_names_prefers_memo_detail() -> None:
    from app.services.meeting_registry_service import resolve_registry_participant_names

    names = resolve_registry_participant_names(
        memo_detail={
            "application": {
                "initiator": {"full_name": "A"},
                "participants": [
                    {"full_name": "Петров Петр Петрович"},
                    {"full_name": "Иванов Иван Иванович"},
                ],
            }
        },
        attendee_details=[
            {"fio": "Комарькова Анастасия Эдуардовна"},
            {"fio": "Мангасарян Давид Каренович"},
        ],
    )
    assert names == [
        "Петров Петр Петрович",
        "Иванов Иван Иванович",
    ]


def test_resolve_registry_participant_names_falls_back_to_attendee_details() -> None:
    from app.services.meeting_registry_service import resolve_registry_participant_names

    names = resolve_registry_participant_names(
        attendee_details=[
            {"fio": "Комарькова Анастасия Эдуардовна"},
            {"fio": "Мангасарян Давид Каренович"},
        ],
    )
    assert names == [
        "Комарькова Анастасия Эдуардовна",
        "Мангасарян Давид Каренович",
    ]


@pytest.mark.asyncio
async def test_get_registry_participants_returns_participants_from_db(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = [
        "Сысоева Ирина Леонидовна",
        "Иванов Иван Иванович",
        "Петров Петр Петрович",
    ]
    entry.participants_count = 3
    entry.updated_at = entry.invitations_sent_at

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)

    with patch("app.services.meeting_service.MeetingRegistryService", return_value=registry):
        result = await service.get_registry_participants(
            entry.memo_ref_key,
            current_user=user,
        )

    assert result.ref_key == entry.memo_ref_key
    assert result.participants_count == 3
    assert result.participants == [
        "Сысоева Ирина Леонидовна",
        "Иванов Иван Иванович",
        "Петров Петр Петрович",
    ]
