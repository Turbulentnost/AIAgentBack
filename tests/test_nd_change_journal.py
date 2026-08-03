from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import nd_control
from app.models.enums import NdChangeJournalEventType, NdChangeJournalSource
from app.models.user import Role, User
from app.services.nd_change_journal_service import NdChangeJournalService


def _user(*, position: str | None = None, role_code: str = "employee") -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.com",
        username=str(uuid.uuid4()),
        hashed_password="hash",
        full_name="Test User",
        position=position,
        is_active=True,
        is_superuser=False,
    )
    user.role = Role(code=role_code, name=role_code, is_system=True)
    return user


def _db() -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = []
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)
    db.scalar = AsyncMock(return_value=0)
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_log_event_creates_entry() -> None:
    db = _db()
    entry = await NdChangeJournalService(db).log_event(
        event_type=NdChangeJournalEventType.TEMPLATE_DOCUMENT_ADDED,
        actor_user_id=uuid.uuid4(),
        resource_type="nd_control_template_document",
        resource_id=uuid.uuid4(),
        summary="Документ добавлен в шаблон",
        source=NdChangeJournalSource.MANUAL,
        payload={"ok": True},
    )

    assert entry.event_type == NdChangeJournalEventType.TEMPLATE_DOCUMENT_ADDED
    assert entry.summary == "Документ добавлен в шаблон"
    db.add.assert_called_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_entries_applies_filters() -> None:
    db = _db()
    await NdChangeJournalService(db).list_entries(
        event_type=NdChangeJournalEventType.ND_CHANGE_REQUEST_CREATED,
        department_id=uuid.uuid4(),
        search="СТО",
        page=2,
        size=10,
    )

    assert db.scalar.await_count == 1
    assert db.execute.await_count == 1
    statement = str(db.execute.await_args.args[0])
    assert "nd_change_journal_entries" in statement


@pytest.mark.asyncio
async def test_regular_user_cannot_view_change_journal() -> None:
    with pytest.raises(HTTPException) as exc:
        await nd_control.list_nd_change_journal(
            _db(),
            _user(position="Инженер"),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_process_specialist_can_view_change_journal(monkeypatch) -> None:
    async def fake_list_entries(self, **kwargs):
        return [], 0

    monkeypatch.setattr(NdChangeJournalService, "list_entries", fake_list_entries)
    page = await nd_control.list_nd_change_journal(
        _db(),
        _user(position="Специалист по процессному управлению"),
        page=1,
        size=50,
    )

    assert page.total == 0
