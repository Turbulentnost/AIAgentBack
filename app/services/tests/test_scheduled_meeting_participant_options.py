from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.scheduled_meeting_service import ScheduledMeetingService


@pytest.mark.asyncio
async def test_list_participant_options_reads_positions() -> None:
    db = AsyncMock()
    position_id = uuid4()

    with patch(
        "app.services.position_service.PositionService.list",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    id=position_id,
                    name="Директор по развитию",
                    slug="директор-по-развитию",
                )
            ]
        ),
    ):
        result = await ScheduledMeetingService(db).list_participant_options(search="развит")

    assert len(result) == 1
    assert result[0].id == position_id
    assert result[0].name == "Директор по развитию"
    assert result[0].slug == "директор-по-развитию"
