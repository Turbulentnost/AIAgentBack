from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.meeting_permission import (
    append_meeting_agent_for_office_management,
    can_access_meeting_agent,
    can_manage_meetings,
    is_office_management_department_name,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Управление делами", True),
        ("(ГК.) Управление делами", False),
        ("Отдел продаж", False),
        (None, False),
        ("", False),
    ],
)
def test_is_office_management_department_name(name: str | None, expected: bool) -> None:
    assert is_office_management_department_name(name) is expected


@pytest.mark.asyncio
async def test_can_access_meeting_agent_for_office_management_user() -> None:
    db = AsyncMock()
    user = MagicMock(is_superuser=False, role=None, department_id=uuid.uuid4())
    user.role = None
    department = SimpleNamespace(name="\u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u0434\u0435\u043b\u0430\u043c\u0438")

    db.get = AsyncMock(return_value=department)
    db.execute = AsyncMock(return_value=MagicMock(all=lambda: []))

    assert await can_access_meeting_agent(db, user) is True


@pytest.mark.asyncio
async def test_can_access_meeting_agent_denied_without_access() -> None:
    db = AsyncMock()
    user = MagicMock(is_superuser=False, role=None, department_id=uuid.uuid4())
    user.role = None
    department = SimpleNamespace(name="\u041e\u0442\u0434\u0435\u043b \u043f\u0440\u043e\u0434\u0430\u0436")

    db.get = AsyncMock(return_value=department)
    db.execute = AsyncMock(return_value=MagicMock(all=lambda: []))
    db.scalar = AsyncMock(return_value=None)

    assert await can_access_meeting_agent(db, user) is False


@pytest.mark.asyncio
async def test_can_manage_meetings_only_for_superuser() -> None:
    db = AsyncMock()
    superuser = MagicMock(is_superuser=True, role=None)
    superuser.role = None
    regular = MagicMock(is_superuser=False, role=None)
    regular.role = None

    db.execute = AsyncMock(return_value=MagicMock(all=lambda: []))

    assert await can_manage_meetings(db, superuser) is True
    assert await can_manage_meetings(db, regular) is False


@pytest.mark.asyncio
async def test_append_meeting_agent_for_office_management() -> None:
    db = AsyncMock()
    user = MagicMock(is_superuser=False, department_id=uuid.uuid4())
    department = SimpleNamespace(name="\u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u0434\u0435\u043b\u0430\u043c\u0438")
    agent = SimpleNamespace(id=uuid.uuid4(), name="Meeting Agent")
    existing = [SimpleNamespace(id=uuid.uuid4(), name="Other Agent")]

    db.get = AsyncMock(return_value=department)
    db.scalar = AsyncMock(return_value=agent)

    result = await append_meeting_agent_for_office_management(db, user, existing)

    assert len(result) == 2
    assert any(item.id == agent.id for item in result)


@pytest.mark.asyncio
async def test_append_meeting_agent_skips_non_office_management() -> None:
    db = AsyncMock()
    user = MagicMock(is_superuser=False, department_id=uuid.uuid4())
    department = SimpleNamespace(name="\u041e\u0442\u0434\u0435\u043b \u043f\u0440\u043e\u0434\u0430\u0436")
    existing = [SimpleNamespace(id=uuid.uuid4(), name="Other Agent")]

    db.get = AsyncMock(return_value=department)

    result = await append_meeting_agent_for_office_management(db, user, existing)

    assert result == existing
