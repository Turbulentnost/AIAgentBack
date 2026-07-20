from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.admin import users as admin_users
from app.models.user import User


def _user(*, superuser: bool) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.com",
        hashed_password="hash",
        is_active=True,
        is_superuser=superuser,
    )


@pytest.mark.asyncio
async def test_regular_user_cannot_delete_user() -> None:
    with pytest.raises(HTTPException) as exc:
        await admin_users.delete_admin_user(AsyncMock(), _user(superuser=False), uuid.uuid4())

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_delete_self() -> None:
    current_user = _user(superuser=True)

    with pytest.raises(HTTPException) as exc:
        await admin_users.delete_admin_user(AsyncMock(), current_user, current_user.id)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_user_soft_deletes_and_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    current_user = _user(superuser=True)
    target_user = _user(superuser=False)
    soft_delete = AsyncMock()
    audit_log = AsyncMock()

    class FakeUserService:
        def __init__(self, db) -> None:
            self.db = db

        async def get(self, user_id: uuid.UUID) -> User | None:
            return target_user if user_id == target_user.id else None

        async def soft_delete(self, user: User) -> User:
            await soft_delete(user)
            return user

    class FakeAuditService:
        def __init__(self, db) -> None:
            self.db = db

        async def log(self, **kwargs) -> None:
            await audit_log(**kwargs)

    monkeypatch.setattr(admin_users, "UserService", FakeUserService)
    monkeypatch.setattr(admin_users, "AuditService", FakeAuditService)

    result = await admin_users.delete_admin_user(AsyncMock(), current_user, target_user.id)

    assert result.status_code == 204
    assert result.body == b""
    soft_delete.assert_awaited_once_with(target_user)
    audit_log.assert_awaited_once()
    assert audit_log.await_args.kwargs["action"] == "admin.users.delete"
    assert audit_log.await_args.kwargs["payload"]["soft_delete"] is True


@pytest.mark.asyncio
async def test_delete_missing_user_returns_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeUserService:
        def __init__(self, db) -> None:
            self.db = db

        async def get(self, user_id: uuid.UUID) -> None:
            return None

    monkeypatch.setattr(admin_users, "UserService", FakeUserService)

    with pytest.raises(HTTPException) as exc:
        await admin_users.delete_admin_user(
            AsyncMock(),
            _user(superuser=True),
            uuid.uuid4(),
        )

    assert exc.value.status_code == 404
