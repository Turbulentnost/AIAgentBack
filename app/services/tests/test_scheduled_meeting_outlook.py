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
    _is_invitable_attendee_email,
    dispatch_scheduled_meeting_invite,
    resolve_attendees,
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


def test_is_invitable_attendee_email_rejects_sync_placeholder() -> None:
    assert _is_invitable_attendee_email(
        "1c+8f027ed9-a80e-11eb-85c7-ac1f6b05524d@enterprise.sync.local"
    ) is False
    assert _is_invitable_attendee_email("svetlana@turbo-don.ru") is True
    assert _is_invitable_attendee_email("user@gmail.com") is False


@pytest.mark.asyncio
@patch("app.services.scheduled_meeting_outlook.lookup_fios_by_position_title")
@patch("app.tools.onec.lookup_email_by_fio.lookup_email_by_fio")
async def test_resolve_attendees_skips_sync_placeholder_and_uses_gal(
    mock_lookup_email,
    mock_lookup_fios,
) -> None:
    position = SimpleNamespace(name="Заместитель по экономике")
    participant = SimpleNamespace(position_id="pos-1", position=position, sort_order=0)
    meeting = _meeting(participants=[participant])

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        return_value=MagicMock(
            all=MagicMock(
                return_value=[
                    (
                        "Соломичева Светлана Викторовна",
                        "1c+8f027ed9-a80e-11eb-85c7-ac1f6b05524d@enterprise.sync.local",
                        "Заместитель по экономике",
                    )
                ]
            )
        )
    )
    mock_lookup_fios.return_value = ["Соломичева Светлана Викторовна"]
    mock_lookup_email.return_value = {
        "emails": [{"email": "solomicheva@turbo-don.ru"}],
    }

    attendees = await resolve_attendees(mock_db, meeting)

    assert attendees == [("Соломичева Светлана Викторовна", "solomicheva@turbo-don.ru")]
    mock_lookup_fios.assert_called_once_with("Заместитель по экономике")
    mock_lookup_email.assert_called_once_with("Соломичева Светлана Викторовна")


@pytest.mark.asyncio
@patch("app.services.scheduled_meeting_outlook.lookup_fios_by_position_title")
@patch("app.tools.onec.lookup_email_by_fio.lookup_email_by_fio")
async def test_resolve_attendees_prefers_db_corporate_email_without_duplicate(
    mock_lookup_email,
    mock_lookup_fios,
) -> None:
    position = SimpleNamespace(name="Заместитель по экономике")
    participant = SimpleNamespace(position_id="pos-1", position=position, sort_order=0)
    meeting = _meeting(participants=[participant])

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        return_value=MagicMock(
            all=MagicMock(
                return_value=[
                    (
                        "Соломичева Светлана Викторовна",
                        "director@turbo-don.ru",
                        "Заместитель по экономике",
                    )
                ]
            )
        )
    )
    mock_lookup_fios.return_value = ["Соломичева Светлана Викторовна"]
    mock_lookup_email.return_value = {
        "emails": [{"email": "director@turbo-don.ru"}],
    }

    attendees = await resolve_attendees(mock_db, meeting)

    assert attendees == [("Соломичева Светлана Викторовна", "director@turbo-don.ru")]
    mock_lookup_email.assert_called_once()
