from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.scheduled_meeting_person import (
    list_employee_options,
    outlook_user_id_for_email,
)


@pytest.mark.asyncio
async def test_list_employee_options_uses_outlook_only() -> None:
    db = AsyncMock()
    gal_candidates = [
        {
            "fio": "Уставицкий Андрей Алексеевич",
            "email": "sktb_razvitie5@turbo-don.ru",
        }
    ]

    with patch(
        "app.tools.onec.exchange_gal_lookup.dispatch_search_exchange_gal_users",
        return_value=gal_candidates,
    ):
        options = await list_employee_options(db, search="устав")

    assert len(options) == 1
    assert options[0].fio == "Уставицкий Андрей Алексеевич"
    assert options[0].email == "sktb_razvitie5@turbo-don.ru"
    assert options[0].id == outlook_user_id_for_email("sktb_razvitie5@turbo-don.ru")
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_list_employee_options_requires_min_three_chars() -> None:
    db = AsyncMock()
    options = await list_employee_options(db, search="ус")
    assert options == []
    db.execute.assert_not_called()
