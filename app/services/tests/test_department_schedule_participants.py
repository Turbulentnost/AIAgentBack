from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.user_service import DepartmentService


@pytest.mark.asyncio
async def test_list_schedule_participant_options_filters_positions_only() -> None:
    db = AsyncMock()
    departments = [
        SimpleNamespace(id="1", name="ФИНАНСОВЫЙ ДИРЕКТОР", is_active=False),
        SimpleNamespace(id="2", name="Отдел информационных технологий", is_active=True),
        SimpleNamespace(id="3", name="(ликв.) Производство", is_active=True),
        SimpleNamespace(id="4", name="Главный инженер", is_active=False),
        SimpleNamespace(id="5", name="Электрик/энергетик", is_active=False),
    ]

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = departments
    db.execute = AsyncMock(return_value=execute_result)

    result = await DepartmentService(db).list_schedule_participant_options()

    assert [item.name for item in result] == [
        "ФИНАНСОВЫЙ ДИРЕКТОР",
        "Главный инженер",
        "Электрик/энергетик",
    ]


@pytest.mark.asyncio
async def test_list_schedule_participant_options_supports_search() -> None:
    db = AsyncMock()
    departments = [
        SimpleNamespace(id="1", name="ФИНАНСОВЫЙ ДИРЕКТОР", is_active=False),
        SimpleNamespace(id="2", name="ОПЕРАЦИОННЫЙ ДИРЕКТОР", is_active=False),
    ]

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = departments
    db.execute = AsyncMock(return_value=execute_result)

    result = await DepartmentService(db).list_schedule_participant_options(search="финан")

    assert len(result) == 1
    assert result[0].name == "ФИНАНСОВЫЙ ДИРЕКТОР"
