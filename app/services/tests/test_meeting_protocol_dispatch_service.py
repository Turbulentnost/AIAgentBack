from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.enums import MeetingRegistryStage
from app.models.meeting_registry import MeetingRegistryEntry
from app.services.meeting_protocol_dispatch_service import MeetingProtocolDispatchService


@pytest.mark.asyncio
async def test_dispatch_schedules_due_entries() -> None:
    db = AsyncMock()
    service = MeetingProtocolDispatchService(db)
    now = datetime.now(timezone.utc)
    entry = MeetingRegistryEntry(
        id=uuid.uuid4(),
        memo_ref_key="memo-1",
        slot_start=now + timedelta(hours=1),
        protocol_draft_at=now + timedelta(minutes=50),
        stage=MeetingRegistryStage.INVITATIONS_SENT,
        invitations_sent_at=now,
    )

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.side_effect = [[entry], []]
    db.execute = AsyncMock(return_value=execute_result)
    service.draft_service.schedule_protocol_draft = AsyncMock(return_value=entry)

    with patch(
        "app.services.meeting_protocol_dispatch_service.settings.MEETING_PROTOCOL_DRAFT_ENABLED",
        True,
    ):
        result = await service.dispatch_due_entries()

    assert result.scheduled == 1
    service.draft_service.schedule_protocol_draft.assert_awaited_once()
