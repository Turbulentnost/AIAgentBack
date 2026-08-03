from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from exchangelib.errors import ErrorItemNotFound

from app.tools.Outlook.cancel_meeting import _ensure_calendar_item, get_meeting_by_id, meeting_to_dict


def test_ensure_calendar_item_rejects_error_item_not_found() -> None:
    with pytest.raises(RuntimeError, match="не найдено"):
        _ensure_calendar_item(ErrorItemNotFound("missing"), context="id test")


def test_get_meeting_by_id_rejects_error_item_not_found() -> None:
    config = SimpleNamespace(timezone="Europe/Moscow")
    account = SimpleNamespace()
    account.fetch = lambda items: [ErrorItemNotFound("missing")]

    with patch("app.tools.Outlook.cancel_meeting.connect_account", return_value=account):
        with pytest.raises(RuntimeError, match="не найдено"):
            get_meeting_by_id(config=config, item_id="AQMkAD-test")


def test_meeting_to_dict_handles_missing_organizer() -> None:
    config = SimpleNamespace(timezone="Europe/Moscow")
    item = SimpleNamespace(
        id="id-1",
        changekey="ck-1",
        subject="Тест",
        start=None,
        end=None,
        location="",
        organizer=None,
        required_attendees=[],
        optional_attendees=[],
        resources=[],
        is_cancelled=False,
    )
    data = meeting_to_dict(item, config=config)
    assert data["id"] == "id-1"
    assert data["organizer"] is None
