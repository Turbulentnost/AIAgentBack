from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.scheduled_meeting_person import EmployeeOption
from app.services.scheduled_meeting_service import ScheduledMeetingService


@pytest.mark.asyncio
async def test_list_employee_options_maps_person_service_results() -> None:
    db = AsyncMock()
    user_id = uuid.uuid4()
    options = [
        EmployeeOption(
            id=user_id,
            fio="Иванов Иван Иванович",
            email="ivanov@turbo-don.ru",
            position_name="Главный инженер",
        )
    ]

    with patch(
        "app.services.scheduled_meeting_service.list_employee_options",
        AsyncMock(return_value=options),
    ):
        result = await ScheduledMeetingService(db).list_employee_options(search="Иванов")

    assert len(result) == 1
    assert result[0].id == user_id
    assert result[0].fio == "Иванов Иван Иванович"
    assert result[0].email == "ivanov@turbo-don.ru"
    assert result[0].position_name == "Главный инженер"
