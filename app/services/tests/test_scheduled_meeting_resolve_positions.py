from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.enterprise_positions_report import normalize_position_title
from app.services.scheduled_meeting_person import resolve_users_for_position_ids
from app.utils.department_classification import normalize_position_name


def _position_key(title: str) -> str:
    return normalize_position_title(normalize_position_name(title))


def _user(
    *,
    user_id: uuid.UUID,
    full_name: str,
    email: str,
    position: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        full_name=full_name,
        email=email,
        position=position,
        deleted_at=None,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_resolve_users_for_position_resolved_when_single_match() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()
    user_id = uuid.uuid4()
    position = SimpleNamespace(
        id=position_id,
        name="Главный инженер",
        is_active=True,
    )
    user = _user(
        user_id=user_id,
        full_name="Иванов И.И.",
        email="ivanov@turbo-don.ru",
        position="Главный инженер",
    )

    db.get = AsyncMock(return_value=position)
    with patch(
        "app.services.scheduled_meeting_person._load_users_by_position_key",
        AsyncMock(return_value={_position_key("Главный инженер"): [user]}),
    ):
        results = await resolve_users_for_position_ids(db, [position_id])

    assert len(results) == 1
    assert results[0].status == "resolved"
    assert results[0].employee is not None
    assert results[0].employee.id == user_id


@pytest.mark.asyncio
async def test_resolve_users_for_position_ambiguous_when_multiple_matches() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()
    position = SimpleNamespace(id=position_id, name="Директор", is_active=True)
    users = [
        _user(
            user_id=uuid.uuid4(),
            full_name="Первый",
            email="first@turbo-don.ru",
            position="Директор",
        ),
        _user(
            user_id=uuid.uuid4(),
            full_name="Второй",
            email="second@turbo-don.ru",
            position="Директор",
        ),
    ]

    db.get = AsyncMock(return_value=position)
    with patch(
        "app.services.scheduled_meeting_person._load_users_by_position_key",
        AsyncMock(return_value={_position_key("Директор"): users}),
    ):
        results = await resolve_users_for_position_ids(db, [position_id])

    assert results[0].status == "ambiguous"
    assert len(results[0].candidates) == 2


@pytest.mark.asyncio
async def test_resolve_users_for_position_empty_when_no_users() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()
    position = SimpleNamespace(id=position_id, name="Секретарь", is_active=True)

    db.get = AsyncMock(return_value=position)
    with patch(
        "app.services.scheduled_meeting_person._load_users_by_position_key",
        AsyncMock(return_value={}),
    ), patch(
        "app.services.scheduled_meeting_person.lookup_fios_by_position_title",
        return_value=[],
    ):
        results = await resolve_users_for_position_ids(db, [position_id])

    assert results[0].status == "empty"


@pytest.mark.asyncio
async def test_resolve_users_for_position_falls_back_to_enterprise_report() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()
    user_id = uuid.uuid4()
    position = SimpleNamespace(id=position_id, name="Директор по развитию", is_active=True)
    user = _user(
        user_id=user_id,
        full_name="Соломичева Светлана Викторовна",
        email="solom@turbo-don.ru",
        position="Менеджер",
    )

    db.get = AsyncMock(return_value=position)
    with patch(
        "app.services.scheduled_meeting_person._load_users_by_position_key",
        AsyncMock(return_value={}),
    ), patch(
        "app.services.scheduled_meeting_person.lookup_fios_by_position_title",
        return_value=["Соломичева Светлана Викторовна"],
    ), patch(
        "app.services.scheduled_meeting_person._list_users_by_fio",
        AsyncMock(return_value=[user]),
    ):
        results = await resolve_users_for_position_ids(db, [position_id])

    assert results[0].status == "resolved"
    assert results[0].employee is not None
    assert results[0].employee.id == user_id


@pytest.mark.asyncio
async def test_resolve_users_for_position_ambiguous_when_enterprise_report_fio_has_duplicates() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()
    position = SimpleNamespace(id=position_id, name="Директор по развитию", is_active=True)
    users = [
        _user(
            user_id=uuid.uuid4(),
            full_name="Соломичева Светлана Викторовна",
            email="first@turbo-don.ru",
            position="Менеджер",
        ),
        _user(
            user_id=uuid.uuid4(),
            full_name="Соломичева Светлана Викторовна",
            email="second@turbo-don.ru",
            position="Менеджер",
        ),
    ]

    db.get = AsyncMock(return_value=position)
    with patch(
        "app.services.scheduled_meeting_person._load_users_by_position_key",
        AsyncMock(return_value={}),
    ), patch(
        "app.services.scheduled_meeting_person.lookup_fios_by_position_title",
        return_value=["Соломичева Светлана Викторовна"],
    ), patch(
        "app.services.scheduled_meeting_person._list_users_by_fio",
        AsyncMock(return_value=users),
    ):
        results = await resolve_users_for_position_ids(db, [position_id])

    assert results[0].status == "ambiguous"
    assert len(results[0].candidates) == 2
