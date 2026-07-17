from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.enums import MeetingRegistryEventType, MeetingRegistryStage
from app.models.meeting_registry import MeetingRegistryEntry, MeetingRegistryEvent
from app.models.user import User
from app.schemas.meeting import (
    MeetingRegistryCancelRequest,
    MeetingRegistryParticipantsApplyRequest,
    MeetingRegistryStageRead,
)
from app.services.meeting_backend import MeetingBackendError, ResolvedParticipant
from app.services.meeting_registry_service import (
    MeetingRegistryService,
    build_stage_counts,
    participant_names_diff,
    stage_index,
)
from app.services.meeting_service import MeetingService, MeetingServiceError


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
    entry.cancelled_at = datetime(2026, 7, 9, 9, 0, tzinfo=timezone.utc)
    cancel_event = MeetingRegistryEvent(
        registry_entry_id=entry.id,
        memo_ref_key=entry.memo_ref_key,
        occurred_at=entry.cancelled_at,
        event_type=MeetingRegistryEventType.CANCELLED,
        message="Совещание отменено",
        payload={"outlook_cancelled": True},
    )

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.list_events = AsyncMock(return_value=[cancel_event])

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
    assert result.outlook_cancelled is True
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
async def test_mark_cancelled_sets_state_and_event(user) -> None:
    db = AsyncMock()
    db.flush = AsyncMock()
    service = MeetingRegistryService(db)

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.payload = {"attendees": ["a@turbo-don.ru"]}
    added: list[object] = []
    db.add = MagicMock(side_effect=lambda item: added.append(item))

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
    assert updated.cancelled_at is not None
    assert updated.payload == {
        "attendees": ["a@turbo-don.ru"],
        "sent_payload": {},
    }
    events = [item for item in added if isinstance(item, MeetingRegistryEvent)]
    assert len(events) == 1
    assert events[0].event_type == MeetingRegistryEventType.CANCELLED
    assert events[0].message == "Отмена"
    assert events[0].payload["outlook_cancelled"] is True


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
    assert updated.cancelled_at is None
    assert updated.outlook_item_id == "new-id"


@pytest.mark.asyncio
async def test_upsert_from_invite_saves_participants_from_memo_detail(user) -> None:
    db = AsyncMock()
    db.flush = AsyncMock()
    service = MeetingRegistryService(db)

    added_entry: MeetingRegistryEntry | None = None

    def capture_add(item: object) -> None:
        nonlocal added_entry
        if isinstance(item, MeetingRegistryEntry):
            added_entry = item

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
    )

    assert added_entry is not None
    assert added_entry.participants == [
        "Мануков Роман Григорьевич",
        "Арсуноев Михаил Магомедович",
        "Грунтовский Дмитрий Дмитриевич",
        "Асланян Артур Карапетович",
    ]
    assert added_entry.participants_count == 4
    assert added_entry.initiator_name == "Мануков Роман Григорьевич"
    events = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], MeetingRegistryEvent)]
    assert len(events) == 1
    assert events[0].event_type == MeetingRegistryEventType.INVITATIONS_SENT


@pytest.mark.asyncio
async def test_list_events_returns_chronological_items(user) -> None:
    db = AsyncMock()
    service = MeetingRegistryService(db)
    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    first = MeetingRegistryEvent(
        registry_entry_id=entry.id,
        memo_ref_key=entry.memo_ref_key,
        occurred_at=datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc),
        event_type=MeetingRegistryEventType.INVITATIONS_SENT,
        message="Отправлены приглашения",
    )
    second = MeetingRegistryEvent(
        registry_entry_id=entry.id,
        memo_ref_key=entry.memo_ref_key,
        occurred_at=datetime(2026, 7, 13, 11, 0, tzinfo=timezone.utc),
        event_type=MeetingRegistryEventType.RESCHEDULED,
        message="Совещание перенесено",
    )
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [first, second]
    db.execute = AsyncMock(return_value=result_mock)

    events = await service.list_events(entry.memo_ref_key)

    assert len(events) == 2
    assert events[0].message == "Отправлены приглашения"
    assert events[1].message == "Совещание перенесено"


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


def test_resolve_registry_participant_names_uses_db_when_entry_exists() -> None:
    from app.services.meeting_registry_service import resolve_registry_participant_names

    entry = SimpleNamespace(
        participants=[
            "Соломичева Светлана Викторовна",
            "Кондратюк Михаела Борисовна",
        ]
    )
    names = resolve_registry_participant_names(
        registry_entry=entry,
        memo_detail={
            "application": {
                "initiator": {"full_name": "Комарькова Анастасия Эдуардовна"},
                "participants": [{"full_name": "Комарькова Анастасия Эдуардовна"}],
            }
        },
        attendee_details=[{"fio": "Комарькова Анастасия Эдуардовна"}],
    )
    assert names == [
        "Соломичева Светлана Викторовна",
        "Кондратюк Михаела Борисовна",
    ]


def test_resolve_registry_participant_names_prefers_memo_detail_without_entry() -> None:
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
    )
    assert names == [
        "A",
        "Петров Петр Петрович",
        "Иванов Иван Иванович",
    ]


def test_resolve_registry_participant_names_prefers_attendee_details_over_memo_detail() -> None:
    from app.services.meeting_registry_service import resolve_registry_participant_names

    memo_detail = {
        "application": {
            "initiator": {"full_name": "Комарькова Анастасия Эдуардовна"},
            "manager": {"full_name": "Соломичева Светлана Викторовна"},
            "participants": [{"full_name": "Кондратюк Михаела Борисовна"}],
        }
    }
    names = resolve_registry_participant_names(
        memo_detail=memo_detail,
        attendee_details=[
            {"fio": "Соломичева Светлана Викторовна"},
            {"fio": "Кондратюк Михаела Борисовна"},
        ],
    )
    assert names == [
        "Соломичева Светлана Викторовна",
        "Кондратюк Михаела Борисовна",
    ]
    assert "Комарькова Анастасия Эдуардовна" not in names


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
    registry.reconcile_participants_from_outlook = AsyncMock(return_value=entry)

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


@pytest.mark.asyncio
async def test_get_registry_participants_keeps_db_list_when_pending_removal_exists(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.initiator_name = "Комарькова Анастасия Эдуардовна"
    entry.manager_name = "Соломичева Светлана Викторовна"
    entry.participants = [
        "Комарькова Анастасия Эдуардовна",
        "Соломичева Светлана Викторовна",
        "Мангасарян Давид Каренович",
        "Азарова Анна Александровна",
    ]
    entry.participants_count = 4
    entry.payload = {
        "attendees": ["a@turbo-don.ru", "b@turbo-don.ru", "c@turbo-don.ru", "d@turbo-don.ru"],
        "pending_removal": {
            "participants": [
                "Комарькова Анастасия Эдуардовна",
                "Соломичева Светлана Викторовна",
                "Мангасарян Давид Каренович",
            ],
            "removed": ["Азарова Анна Александровна"],
            "attendees": ["a@turbo-don.ru", "b@turbo-don.ru", "c@turbo-don.ru"],
        },
    }
    entry.updated_at = entry.invitations_sent_at

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.reconcile_participants_from_outlook = AsyncMock(return_value=entry)

    with patch("app.services.meeting_service.MeetingRegistryService", return_value=registry):
        result = await service.get_registry_participants(
            entry.memo_ref_key,
            current_user=user,
        )

    assert result.participants_count == 4
    assert result.participants == [
        "Комарькова Анастасия Эдуардовна",
        "Соломичева Светлана Викторовна",
        "Мангасарян Давид Каренович",
        "Азарова Анна Александровна",
    ]
    assert result.pending_confirmation is True
    assert result.pending_removed == ["Азарова Анна Александровна"]
    assert result.pending_participants == [
        "Комарькова Анастасия Эдуардовна",
        "Соломичева Светлана Викторовна",
        "Мангасарян Давид Каренович",
    ]


@pytest.mark.asyncio
async def test_search_registry_participant_found_and_can_add(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = ["Сысоева Ирина Леонидовна"]
    entry.participants_count = 1

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)

    candidates = [
        {"fio": "Иванов Иван Иванович", "email": "ivanov@turbo-don.ru"},
    ]

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch(
            "app.services.meeting_service.dispatch_search_exchange_gal_users",
            return_value=candidates,
        ),
        patch(
            "app.services.meeting_service.pick_exact_exchange_gal_user",
            return_value=candidates[0],
        ),
    ):
        result = await service.search_registry_participant(
            entry.memo_ref_key,
            "Иванов Иван Иванович",
            current_user=user,
        )

    assert result.query == "Иванов Иван Иванович"
    assert result.found is True
    assert result.email == "ivanov@turbo-don.ru"
    assert result.already_added is False
    assert result.can_add is True
    assert len(result.suggestions) == 1


@pytest.mark.asyncio
async def test_search_registry_participant_unique_partial_match_is_found(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = []

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)

    candidates = [
        {
            "fio": "Уставицкий Сергей Владимирович",
            "email": "sktb_razvitie5@turbo-don.ru",
        },
    ]

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch(
            "app.services.meeting_service.dispatch_search_exchange_gal_users",
            return_value=candidates,
        ),
        patch(
            "app.services.meeting_service.pick_exact_exchange_gal_user",
            return_value=candidates[0],
        ),
    ):
        result = await service.search_registry_participant(
            entry.memo_ref_key,
            "уставицкий",
            current_user=user,
        )

    assert result.found is True
    assert result.can_add is True
    assert result.fio == "Уставицкий Сергей Владимирович"
    assert result.message is None


@pytest.mark.asyncio
async def test_search_registry_participant_returns_suggestions_for_partial_query(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = []

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)

    candidates = [
        {
            "fio": "Комарькова Анастасия Эдуардовна",
            "email": "a.komarkova@turbo-don.ru",
        },
        {
            "fio": "Комарькова Мария Сергеевна",
            "email": "m.komarkova@turbo-don.ru",
        },
    ]

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch(
            "app.services.meeting_service.dispatch_search_exchange_gal_users",
            return_value=candidates,
        ),
        patch(
            "app.services.meeting_service.pick_exact_exchange_gal_user",
            return_value=None,
        ),
    ):
        result = await service.search_registry_participant(
            entry.memo_ref_key,
            "комарькова",
            current_user=user,
        )

    assert result.found is False
    assert result.can_add is False
    assert result.email is None
    assert len(result.suggestions) == 2
    assert result.suggestions[0].fio == "Комарькова Анастасия Эдуардовна"
    assert result.message == "Выберите участника из списка"


@pytest.mark.asyncio
async def test_search_registry_participant_not_found(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = []

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch(
            "app.services.meeting_service.dispatch_search_exchange_gal_users",
            return_value=[],
        ),
        patch(
            "app.services.meeting_service.pick_exact_exchange_gal_user",
            return_value=None,
        ),
    ):
        result = await service.search_registry_participant(
            entry.memo_ref_key,
            "Неизвестный Пользователь",
            current_user=user,
        )

    assert result.found is False
    assert result.can_add is False
    assert result.email is None
    assert result.suggestions == []
    assert result.message == "Не найден в Outlook"


@pytest.mark.asyncio
async def test_search_registry_participant_already_added(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = ["Иванов Иван Иванович"]

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)

    candidates = [
        {"fio": "Иванов Иван Иванович", "email": "ivanov@turbo-don.ru"},
    ]

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch(
            "app.services.meeting_service.dispatch_search_exchange_gal_users",
            return_value=candidates,
        ),
        patch(
            "app.services.meeting_service.pick_exact_exchange_gal_user",
            return_value=candidates[0],
        ),
    ):
        result = await service.search_registry_participant(
            entry.memo_ref_key,
            "Иванов Иван Иванович",
            current_user=user,
        )

    assert result.found is True
    assert result.already_added is True
    assert result.can_add is False


@pytest.mark.asyncio
async def test_cancel_registry_participants_removal_clears_pending(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()
    service.audit.log = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = [
        "Комарькова Анастасия Эдуардовна",
        "Соломичева Светлана Викторовна",
        "Кондратюк Михаела Борисовна",
    ]
    entry.participants_count = 3
    entry.payload = {
        "attendees": ["a@turbo-don.ru", "b@turbo-don.ru", "c@turbo-don.ru"],
        "pending_removal": {
            "participants": [
                "Комарькова Анастасия Эдуардовна",
                "Соломичева Светлана Викторовна",
            ],
            "removed": ["Кондратюк Михаела Борисовна"],
            "attendees": ["a@turbo-don.ru", "b@turbo-don.ru"],
        },
    }
    entry.updated_at = entry.invitations_sent_at

    cleared_entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    cleared_entry.participants = entry.participants
    cleared_entry.participants_count = entry.participants_count
    cleared_entry.payload = {"attendees": entry.payload["attendees"]}
    cleared_entry.updated_at = entry.updated_at

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.clear_pending_removal = AsyncMock(return_value=cleared_entry)

    with patch("app.services.meeting_service.MeetingRegistryService", return_value=registry):
        result = await service.cancel_registry_participants_removal(
            entry.memo_ref_key,
            current_user=user,
        )

    registry.clear_pending_removal.assert_awaited_once()
    service.audit.log.assert_awaited_once()
    assert result.pending_confirmation is False
    assert result.pending_removed == []
    assert result.participants_count == 3
    assert "Кондратюк Михаела Борисовна" in result.participants


@pytest.mark.asyncio
async def test_get_registry_history_returns_events(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    event = MeetingRegistryEvent(
        registry_entry_id=entry.id,
        memo_ref_key=entry.memo_ref_key,
        occurred_at=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
        event_type=MeetingRegistryEventType.INVITATIONS_SENT,
        message="Отправлены приглашения",
    )

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)
    registry.list_events = AsyncMock(return_value=[event])

    with patch("app.services.meeting_service.MeetingRegistryService", return_value=registry):
        result = await service.get_registry_history(
            entry.memo_ref_key,
            current_user=user,
        )

    assert result.ref_key == entry.memo_ref_key
    assert len(result.events) == 1
    assert result.events[0].message == "Отправлены приглашения"
    assert result.events[0].event_type.value == "invitations_sent"


def test_participant_names_diff_detects_added_and_removed() -> None:
    added, removed = participant_names_diff(
        ["Иванов Иван Иванович", "Петров Петр Петрович"],
        ["Иванов Иван Иванович", "Сидоров Сидор Сидорович"],
    )
    assert removed == ["Петров Петр Петрович"]
    assert added == ["Сидоров Сидор Сидорович"]


@pytest.mark.asyncio
async def test_apply_registry_participants_updates_db_and_outlook(user) -> None:
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
            ResolvedParticipant(
                fio="Сидоров Сидор Сидорович",
                email="sidorov@turbo-don.ru",
                found=True,
            ),
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

    outlook_mock.assert_called_once()
    registry.apply_participants_update.assert_awaited_once()
    assert result.added == ["Сидоров Сидор Сидорович"]
    assert result.removed == ["Петров Петр Петрович"]
    assert result.outlook_updated is True
    assert result.participants_count == 2
    assert result.earlier_slot_suggestion is None


@pytest.mark.asyncio
async def test_apply_registry_participants_fails_when_added_email_missing(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock()

    entry = _entry(MeetingRegistryStage.INVITATIONS_SENT)
    entry.participants = ["Иванов Иван Иванович"]
    entry.payload = {"attendees": ["ivanov@turbo-don.ru"]}

    registry = MagicMock()
    registry.get_entry = AsyncMock(return_value=entry)

    backend = MagicMock()
    backend.resolve_participants = AsyncMock(
        return_value=[
            ResolvedParticipant(fio="Неизвестный Участник", email=None, found=False),
        ]
    )

    with (
        patch("app.services.meeting_service.MeetingRegistryService", return_value=registry),
        patch.object(MeetingService, "_backend", return_value=backend),
    ):
        with pytest.raises(MeetingServiceError) as exc_info:
            await service.apply_registry_participants(
                entry.memo_ref_key,
                MeetingRegistryParticipantsApplyRequest(
                    participants=["Иванов Иван Иванович", "Неизвестный Участник"],
                    added=["Неизвестный Участник"],
                    removed=[],
                ),
                current_user=user,
            )

    assert exc_info.value.status_code == 400
