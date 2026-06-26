from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.user import Role, User
from app.services.nd_control_permission import (
    can_access_nd_control_agent,
    can_manage_nd_control_departments,
    can_manage_nd_control_templates,
    can_reanalyze_nd_control_departments,
    can_upload_template_documents,
    can_view_nd_change_journal,
    is_process_management_specialist_position,
)


def _user(*, position: str | None = None, role_code: str = "employee", superuser: bool = False) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.com",
        username=str(uuid.uuid4()),
        hashed_password="hash",
        full_name="Test User",
        position=position,
        is_active=True,
        is_superuser=superuser,
    )
    user.role = Role(code=role_code, name=role_code, is_system=True)
    return user


def _db() -> AsyncMock:
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.all.return_value = []
    db.execute = AsyncMock(return_value=execute_result)
    db.scalar = AsyncMock(return_value=None)
    return db


@pytest.mark.parametrize(
    "position",
    [
        "Специалист по процессному управлению",
        "ведущий специалист по процессному управлению",
        "Специалист отдела процессного управления",
        "специалист по процессному   управлению",
    ],
)
def test_detects_process_management_specialist_position(position: str) -> None:
    assert is_process_management_specialist_position(position)


@pytest.mark.asyncio
async def test_process_specialist_can_access_agent_and_manage_templates_but_not_departments_or_reanalyze() -> None:
    db = _db()
    user = _user(position="Специалист по процессному управлению")

    assert await can_manage_nd_control_templates(db, user)
    assert await can_upload_template_documents(db, user)
    assert not await can_manage_nd_control_departments(db, user)
    assert not await can_reanalyze_nd_control_departments(db, user)
    assert await can_view_nd_change_journal(db, user)
    assert await can_access_nd_control_agent(db, user)


@pytest.mark.asyncio
async def test_regular_agent_user_has_read_only_nd_permissions(monkeypatch) -> None:
    db = _db()
    db.scalar = AsyncMock(return_value=MagicMock())
    user = _user(position="Инженер")

    async def can_access_agent(self, user, agent_id, action="run"):
        return True

    monkeypatch.setattr(
        "app.services.permission_service.PermissionService.can_access_agent",
        can_access_agent,
    )

    assert await can_access_nd_control_agent(db, user)
    assert not await can_manage_nd_control_templates(db, user)
    assert not await can_upload_template_documents(db, user)
    assert not await can_manage_nd_control_departments(db, user)
    assert not await can_reanalyze_nd_control_departments(db, user)
    assert not await can_view_nd_change_journal(db, user)


@pytest.mark.asyncio
async def test_quality_deputy_can_departments_reanalyze_and_journal_but_not_template_write() -> None:
    db = _db()
    user = _user(position="Заместитель технического директора по качеству")

    assert await can_manage_nd_control_departments(db, user)
    assert await can_reanalyze_nd_control_departments(db, user)
    assert await can_view_nd_change_journal(db, user)
    assert not await can_manage_nd_control_templates(db, user)
    assert not await can_upload_template_documents(db, user)
    assert await can_access_nd_control_agent(db, user)


@pytest.mark.asyncio
async def test_admin_can_all_nd_control_permissions() -> None:
    db = _db()
    user = _user(role_code="admin")

    assert await can_manage_nd_control_departments(db, user)
    assert await can_reanalyze_nd_control_departments(db, user)
    assert await can_manage_nd_control_templates(db, user)
    assert await can_upload_template_documents(db, user)
    assert await can_view_nd_change_journal(db, user)
    assert await can_access_nd_control_agent(db, user)
