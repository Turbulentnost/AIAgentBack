from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.enums import MeetingRegistryStage
from app.models.meeting_registry import MeetingRegistryEntry
from app.services.meeting_protocol_draft_service import (
    MeetingProtocolDraftService,
    build_protocol_number_stub,
    compute_protocol_draft_at,
    read_meeting_topic,
)


def test_compute_protocol_draft_at() -> None:
    slot_start = datetime(2026, 7, 22, 14, 30, tzinfo=timezone.utc)
    assert compute_protocol_draft_at(slot_start, minutes_before=10) == datetime(
        2026, 7, 22, 14, 20, tzinfo=timezone.utc
    )


def test_build_protocol_number_stub() -> None:
    entry = MeetingRegistryEntry(
        memo_ref_key="abc-123",
        memo_number="000001234",
        slot_start=datetime(2026, 7, 22, 14, 30, tzinfo=timezone.utc),
        invitations_sent_at=datetime.now(timezone.utc),
    )
    number = build_protocol_number_stub(entry)
    assert "000001234" in number
    assert "20260722" in number


def test_read_topic_department_key_from_keys() -> None:
    from app.services.meeting_protocol_draft_service import read_topic_department_key

    assert read_topic_department_key({"keys": {"department": "dept-1"}}) == "dept-1"
    assert read_topic_department_key({"department_key": "dept-2"}) == "dept-2"
    assert read_topic_department_key({}) is None


def test_read_meeting_topic_from_payload() -> None:
    entry = MeetingRegistryEntry(
        memo_ref_key="abc",
        invitations_sent_at=datetime.now(timezone.utc),
        payload={"meeting_topic": {"ref_key": "topic-1", "description": "тест"}},
    )
    assert read_meeting_topic(entry) == {"ref_key": "topic-1", "description": "тест"}


@pytest.mark.asyncio
async def test_schedule_protocol_draft_sets_celery_task_id() -> None:
    db = AsyncMock()
    service = MeetingProtocolDraftService(db)
    now = datetime.now(timezone.utc)
    entry = MeetingRegistryEntry(
        id=uuid.uuid4(),
        memo_ref_key="memo-1",
        slot_start=now + timedelta(hours=2),
        stage=MeetingRegistryStage.INVITATIONS_SENT,
        invitations_sent_at=now,
    )

    with (
        patch(
            "app.services.meeting_protocol_draft_service.settings.MEETING_PROTOCOL_DRAFT_ENABLED",
            True,
        ),
        patch(
            "app.services.meeting_protocol_draft_service.enqueue_protocol_draft_task",
            return_value="celery-task-1",
        ) as enqueue_mock,
    ):
        result = await service.schedule_protocol_draft(entry, force=True)

    assert result.protocol_draft_celery_task_id == "celery-task-1"
    assert result.protocol_draft_at is not None
    enqueue_mock.assert_called_once()


@pytest.mark.asyncio
async def test_create_protocol_draft_skips_without_topic() -> None:
    db = AsyncMock()
    service = MeetingProtocolDraftService(db)
    entry_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    entry = MeetingRegistryEntry(
        id=entry_id,
        memo_ref_key="memo-1",
        slot_start=now + timedelta(minutes=5),
        protocol_draft_at=now - timedelta(minutes=1),
        stage=MeetingRegistryStage.INVITATIONS_SENT,
        invitations_sent_at=now,
        payload={},
    )
    service.get_entry = AsyncMock(return_value=entry)

    result = await service.create_protocol_draft_for_entry(entry_id)

    assert result["skipped"] is True
    assert result["reason"] == "missing_meeting_topic"
    assert "Тема совещания не сохранена" in (entry.protocol_draft_error or "")


@pytest.mark.asyncio
async def test_create_protocol_draft_success() -> None:
    db = AsyncMock()
    service = MeetingProtocolDraftService(db)
    entry_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    entry = MeetingRegistryEntry(
        id=entry_id,
        memo_ref_key="memo-1",
        manager_name="Manager",
        subject="Совещание",
        slot_start=now + timedelta(minutes=5),
        protocol_draft_at=now - timedelta(minutes=1),
        stage=MeetingRegistryStage.INVITATIONS_SENT,
        invitations_sent_at=now,
        payload={"meeting_topic": {"ref_key": "topic-1", "meeting_type": "Отчетное"}},
    )
    service.get_entry = AsyncMock(return_value=entry)
    service.registry = MagicMock()
    service.registry.append_event = AsyncMock()

    with (
        patch(
            "app.services.meeting_protocol_draft_service.resolve_topic_department_key",
            AsyncMock(return_value="dept-1"),
        ),
        patch(
            "app.services.meeting_protocol_draft_service.create_meeting_protocol",
            return_value={
                "protocol": {"ref_key": "proto-1", "number": "НСР_001_О_042"},
            },
        ) as create_mock,
    ):
        result = await service.create_protocol_draft_for_entry(entry_id)

    assert result["created"] is True
    assert entry.protocol_ref_key == "proto-1"
    assert entry.protocol_number == "НСР_001_О_042"
    assert entry.stage == MeetingRegistryStage.PROTOCOL_CREATED
    create_mock.assert_called_once()
    assert "number" not in create_mock.call_args.kwargs
    assert create_mock.call_args.kwargs["department_key"] == "dept-1"


@pytest.mark.asyncio
async def test_recreate_protocol_draft_on_reschedule_deletes_existing() -> None:
    db = AsyncMock()
    service = MeetingProtocolDraftService(db)
    now = datetime.now(timezone.utc)
    entry = MeetingRegistryEntry(
        id=uuid.uuid4(),
        memo_ref_key="memo-1",
        protocol_ref_key="proto-old",
        protocol_number="OLD_001",
        protocol_draft_celery_task_id="old-task",
        slot_start=now + timedelta(hours=3),
        stage=MeetingRegistryStage.PROTOCOL_CREATED,
        invitations_sent_at=now,
    )
    service.cancel_protocol_draft_schedule = AsyncMock(return_value=entry)
    service.refresh_protocol_draft_schedule = AsyncMock(return_value=entry)

    with patch(
        "app.services.meeting_protocol_draft_service.delete_meeting_protocol",
        return_value={"deleted": True},
    ) as delete_mock:
        result = await service.recreate_protocol_draft_on_reschedule(entry)

    delete_mock.assert_called_once()
    assert result.protocol_ref_key is None
    service.refresh_protocol_draft_schedule.assert_awaited_once()
