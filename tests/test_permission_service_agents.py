"""Tests for PermissionService.list_available_agents union query building."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.user import User
from app.services.permission_service import PermissionService


def _user(*, department_id: uuid.UUID | None = None) -> User:
    user = User(
        id=uuid.uuid4(),
        email="user@example.com",
        username="user",
        hashed_password="hash",
        full_name="Test User",
        is_active=True,
         )
    user.department_id = department_id
    user.is_superuser = False
    user.role_id = uuid.uuid4()
    return user


@pytest.mark.asyncio
async def test_list_available_agents_unions_department_and_role_queries() -> None:
    """Users with a department must not hit CompoundSelect.union AttributeError."""
    db = AsyncMock()
    department_id = uuid.uuid4()
    user = _user(department_id=department_id)

    agent = MagicMock()
    agent.name = "Demo agent"

    execute_result = MagicMock()
    execute_result.scalars.return_value.unique.return_value.all.return_value = [agent]
    db.execute = AsyncMock(return_value=execute_result)

    service = PermissionService(db)
    agents = await service.list_available_agents(user)

    assert agents == [agent]
    db.execute.assert_awaited_once()
    compiled = str(db.execute.await_args.args[0])
    assert "UNION" in compiled.upper()
