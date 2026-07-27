from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.scheduled_meeting_person import (
    _is_sync_placeholder_email,
    _persist_corporate_email,
    resolve_person_by_fio,
)
from app.services.scheduled_meeting_person import ScheduledMeetingPersonError


def test_is_sync_placeholder_email() -> None:
    assert _is_sync_placeholder_email(
        "1c+abcd@enterprise.sync.local"
    )
    assert not _is_sync_placeholder_email("user@turbo-don.ru")
    assert not _is_sync_placeholder_email("")


@pytest.mark.asyncio
async def test_persist_corporate_email_replaces_sync_placeholder() -> None:
    user = SimpleNamespace(
        id=uuid.uuid4(),
        email="1c+abcd@enterprise.sync.local",
    )
    db = AsyncMock()
    with patch(
        "app.services.scheduled_meeting_person._resolve_user_by_email",
        AsyncMock(return_value=None),
    ):
        await _persist_corporate_email(db, user, "td_buh3@turbo-don.ru")
    assert user.email == "td_buh3@turbo-don.ru"


@pytest.mark.asyncio
async def test_persist_corporate_email_keeps_existing_corporate() -> None:
    user = SimpleNamespace(
        id=uuid.uuid4(),
        email="already@turbo-don.ru",
    )
    db = AsyncMock()
    await _persist_corporate_email(db, user, "other@turbo-don.ru")
    assert user.email == "already@turbo-don.ru"


@pytest.mark.asyncio
async def test_resolve_person_by_fio_uses_outlook_not_sync_local() -> None:
    user_id = uuid.uuid4()
    position_id = uuid.uuid4()
    user = SimpleNamespace(
        id=user_id,
        email="1c+abcd@enterprise.sync.local",
        full_name="Бутова Татьяна Николаевна",
        position="Главный бухгалтер",
        deleted_at=None,
        is_active=True,
    )
    db = AsyncMock()

    with (
        patch(
            "app.services.scheduled_meeting_person._resolve_user_by_fio",
            AsyncMock(return_value=user),
        ),
        patch(
            "app.services.scheduled_meeting_person._invitable_email_for_user",
            AsyncMock(return_value="td_buh3@turbo-don.ru"),
        ) as invitable,
        patch(
            "app.services.scheduled_meeting_person.resolve_position_id_for_user",
            AsyncMock(return_value=position_id),
        ),
    ):
        person = await resolve_person_by_fio(db, "Бутова Татьяна Николаевна")

    assert person.email == "td_buh3@turbo-don.ru"
    assert person.user_id == user_id
    invitable.assert_awaited_once()
    assert invitable.await_args.kwargs.get("db") is db


@pytest.mark.asyncio
async def test_resolve_person_by_fio_rejects_missing_corporate_email() -> None:
    user = SimpleNamespace(
        id=uuid.uuid4(),
        email="1c+abcd@enterprise.sync.local",
        full_name="Кто-то",
        position=None,
        deleted_at=None,
        is_active=True,
    )
    db = AsyncMock()

    with (
        patch(
            "app.services.scheduled_meeting_person._resolve_user_by_fio",
            AsyncMock(return_value=user),
        ),
        patch(
            "app.services.scheduled_meeting_person._invitable_email_for_user",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(ScheduledMeetingPersonError, match="корпоративного e-mail"):
            await resolve_person_by_fio(db, "Кто-то")
