from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.scheduled_meeting_person import (
    ResolvedPerson,
    resolve_position_id_for_person,
)
from app.services.scheduled_meeting_service import (
    ScheduledMeetingService,
    ScheduledMeetingServiceError,
)


@pytest.mark.asyncio
async def test_resolve_position_id_for_person_uses_position_name() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()
    person = ResolvedPerson(
        user_id=uuid.uuid4(),
        fio="Кондратюк Михаела Борисовна",
        email="kondratyuk@turbo-don.ru",
        position_name="Ведущий менеджер по развитию",
    )

    with patch(
        "app.services.scheduled_meeting_person.resolve_position_id_for_title",
        AsyncMock(return_value=position_id),
    ) as resolve_title:
        result = await resolve_position_id_for_person(db, person)

    assert result == position_id
    resolve_title.assert_awaited_once_with(db, "Ведущий менеджер по развитию")


@pytest.mark.asyncio
async def test_require_position_id_falls_back_to_position_name() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()
    person = ResolvedPerson(
        user_id=uuid.uuid4(),
        fio="Кондратюк Михаела Борисовна",
        email="kondratyuk@turbo-don.ru",
        position_name="Ведущий менеджер по развитию",
    )
    service = ScheduledMeetingService(db)

    with patch.object(
        service,
        "_ensure_positions_exist",
        AsyncMock(),
    ), patch(
        "app.services.scheduled_meeting_service.resolve_position_id_for_person",
        AsyncMock(return_value=position_id),
    ):
        result = await service._require_position_id(
            person,
            role_label="ответственного",
        )

    assert result == position_id


@pytest.mark.asyncio
async def test_require_position_id_raises_when_unresolved() -> None:
    db = AsyncMock()
    person = ResolvedPerson(
        user_id=uuid.uuid4(),
        fio="Неизвестный Сотрудник",
        email="unknown@turbo-don.ru",
    )
    service = ScheduledMeetingService(db)

    with patch(
        "app.services.scheduled_meeting_service.resolve_position_id_for_person",
        AsyncMock(return_value=None),
    ):
        with pytest.raises(ScheduledMeetingServiceError, match="ответственного"):
            await service._require_position_id(
                person,
                role_label="ответственного",
            )
