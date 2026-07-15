from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.enums import (
    ScheduledMeetingFrequency,
    ScheduledMeetingStatus,
    ScheduledMeetingType,
)
from app.services.scheduled_meeting_outlook import (
    ScheduledMeetingOutlookError,
    _invite_body,
    dispatch_scheduled_meeting_invite,
)


def _meeting(**kwargs) -> SimpleNamespace:
    defaults = {
        "title": "Технический совет",
        "meeting_type": ScheduledMeetingType.PLANNED,
        "status": ScheduledMeetingStatus.CREATED,
        "time_local": time(9, 0),
        "duration_minutes": 60,
        "frequency": ScheduledMeetingFrequency.DAILY,
        "interval": 1,
        "monthly_mode": None,
        "day_of_month": None,
        "weekday": None,
        "weekday_position": None,
        "series_start_date": date(2026, 7, 15),
        "series_end_date": date(2026, 7, 17),
        "payload": None,
        "participants": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@patch("app.services.scheduled_meeting_outlook.dispatch_recurring_meeting_invite")
@patch("app.services.scheduled_meeting_outlook.load_config")
def test_dispatch_scheduled_meeting_invite_daily(mock_load_config, mock_dispatch) -> None:
    mock_load_config.return_value = SimpleNamespace(timezone="Europe/Moscow")
    mock_dispatch.return_value = {
        "outlook_item_id": "series-1",
        "outlook_changekey": "ck-1",
        "outlook_meeting_url": "https://outlook.example/item",
    }

    result = dispatch_scheduled_meeting_invite(
        _meeting(),
        attendees=[("Соломичева Светлана Викторовна", "director@turbo-don.ru")],
    )

    assert result["outlook_item_id"] == "series-1"
    mock_dispatch.assert_called_once()
    kwargs = mock_dispatch.call_args.kwargs
    assert kwargs["pattern"] == "daily"
    assert kwargs["end_type"] == "end_date"
    assert kwargs["end"] == "2026-07-17"
    assert kwargs["attendees"] == ["director@turbo-don.ru"]
    assert "Соломичева Светлана Викторовна <director@turbo-don.ru>" in kwargs["body"]
    assert "Совещание запланировано ИИ-агентом" in kwargs["body"]


def test_invite_body_lists_participants_without_comment() -> None:
    meeting = _meeting(
        payload={"comment": "это первый тест"},
    )
    body = _invite_body(
        meeting,
        [
            ("Соломичева Светлана Викторовна", "sv@turbo-don.ru"),
            ("Иванов Иван Иванович", "ii@turbo-don.ru"),
        ],
    )
    assert "это первый тест" not in body
    assert "Соломичева Светлана Викторовна <sv@turbo-don.ru>;" in body
    assert "Иванов Иван Иванович <ii@turbo-don.ru>" in body
    assert "Совещание запланировано ИИ-агентом" in body


@patch("app.services.scheduled_meeting_outlook.dispatch_recurring_meeting_invite")
@patch("app.services.scheduled_meeting_outlook.load_config")
def test_dispatch_scheduled_meeting_invite_rejects_empty_attendees(
    mock_load_config,
    mock_dispatch,
) -> None:
    mock_load_config.return_value = SimpleNamespace(timezone="Europe/Moscow")

    with pytest.raises(ScheduledMeetingOutlookError, match="e-mail"):
        dispatch_scheduled_meeting_invite(_meeting(), attendees=[])

    mock_dispatch.assert_not_called()
