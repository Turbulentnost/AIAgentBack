from __future__ import annotations

import pytest

from app.tools.schemas import ToolContext
from app.tools.system_tools import GetCurrentDateInput, get_current_date, resolve_current_date


def test_resolve_current_date_moscow():
    result = resolve_current_date("Europe/Moscow")
    assert result.date_iso
    assert result.weekday_ru


@pytest.mark.asyncio
async def test_get_current_date_moscow():
    result = await get_current_date(GetCurrentDateInput(timezone="Europe/Moscow"), context=None)  # type: ignore[arg-type]
    assert result.date_iso
    assert result.weekday_ru
    assert "Europe/Moscow" == result.timezone
    assert len(result.date_iso) == 10


@pytest.mark.asyncio
async def test_get_current_date_invalid_timezone():
    with pytest.raises(ValueError, match="timezone"):
        await get_current_date(GetCurrentDateInput(timezone="Invalid/Zone"), context=None)  # type: ignore[arg-type]
