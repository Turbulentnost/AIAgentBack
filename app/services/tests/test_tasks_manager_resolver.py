from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.tasks_manager_resolver import (
    PSD_ASSISTANT_MANAGER_FIO,
    PSD_ASSISTANT_ROLE_CODE,
    is_psd_assistant_role,
    resolve_manager_fio_from_roles,
    resolve_porucheniya_manager_fio,
)


def test_is_psd_assistant_role_by_code() -> None:
    assert is_psd_assistant_role(code=PSD_ASSISTANT_ROLE_CODE, name="anything")


def test_is_psd_assistant_role_by_name() -> None:
    assert is_psd_assistant_role(code="employee", name="Помощник ПСД")


def test_resolve_manager_fio_from_psd_assistant_role() -> None:
    assert (
        resolve_manager_fio_from_roles([(PSD_ASSISTANT_ROLE_CODE, "Помощник ПСД")])
        == PSD_ASSISTANT_MANAGER_FIO
    )


def test_resolve_manager_fio_from_other_role_uses_full_name_path() -> None:
    assert resolve_manager_fio_from_roles([("employee", "Сотрудник")]) is None


@pytest.mark.asyncio
async def test_resolve_porucheniya_manager_fio_for_psd_assistant() -> None:
    user = MagicMock(
        id=uuid.uuid4(),
        role_id=uuid.uuid4(),
        role=MagicMock(code=PSD_ASSISTANT_ROLE_CODE, name="Помощник ПСД"),
        full_name="Иванова Анна Петровна",
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.execute = AsyncMock(return_value=MagicMock(all=lambda: []))

    fio, source = await resolve_porucheniya_manager_fio(db, user)

    assert fio == PSD_ASSISTANT_MANAGER_FIO
    assert source == f"role:{PSD_ASSISTANT_ROLE_CODE}"


@pytest.mark.asyncio
async def test_resolve_porucheniya_manager_fio_for_manager_user() -> None:
    user = MagicMock(
        id=uuid.uuid4(),
        role_id=uuid.uuid4(),
        role=MagicMock(code="employee", name="Сотрудник"),
        full_name="Амураль Игорь Борисович",
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.execute = AsyncMock(return_value=MagicMock(all=lambda: []))

    fio, source = await resolve_porucheniya_manager_fio(db, user)

    assert fio == "Амураль Игорь Борисович"
    assert source == "user_full_name"
