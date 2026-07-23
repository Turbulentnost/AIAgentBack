from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import procurement_permission as perm


def test_engineer_position_normalization():
    assert perm.is_production_preparation_engineer_position("Ведущий инженер по подготовке производства")
    assert perm.is_production_preparation_engineer_position("Инженер СПП")
    assert not perm.is_production_preparation_engineer_position("Инженер ОМТО")
    assert not perm.is_production_preparation_engineer_position("Менеджер по сопровождению ОМТО")


def test_omto_position_normalization():
    assert perm.is_omto_support_manager_position("Менеджер по сопровождению ОМТО")
    assert perm.is_omto_support_manager_position("менеджер омто")
    assert perm.is_omto_support_manager_position("Специалист ОМТО")
    assert not perm.is_omto_support_manager_position("Инженер по подготовке производства")
    assert not perm.is_omto_support_manager_position("Инженер СПП")
    assert not perm.is_omto_support_manager_position(None)
    assert not perm.is_omto_support_manager_position("")


def test_procurement_manager_position_normalization():
    assert perm.is_procurement_manager_position("Ведущий менеджер по закупкам")
    assert perm.is_procurement_manager_position("Начальник ОМТО")
    assert not perm.is_procurement_manager_position("Инженер по качеству")


@pytest.mark.asyncio
async def test_auth_disabled_grants_procurement_manager(monkeypatch):
    monkeypatch.setattr(perm, "_auth_disabled_dev", lambda: True)
    user = SimpleNamespace(is_superuser=False, position="Developer")
    db = AsyncMock()
    assert await perm.can_access_procurement_manager(db, user) is True
    db.scalar.assert_not_called()
