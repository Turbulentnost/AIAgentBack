from __future__ import annotations

import uuid
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
    _emails_for_removed_position_in_series,
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
    # Ежедневно → weekly пн–пт (иначе Outlook ставит сб/вс)
    assert kwargs["pattern"] == "weekly"
    assert kwargs["weekdays"] == [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
    ]
    assert kwargs["interval"] == 1
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
async def test_resolve_attendees_uses_stored_email() -> None:
    participant = SimpleNamespace(
        user_id=uuid.uuid4(),
        person_fio="Соломичева Светлана Викторовна",
        person_email="solomicheva@turbo-don.ru",
        sort_order=0,
    )
    meeting = _meeting(participants=[participant])

    attendees = await resolve_attendees(AsyncMock(), meeting)

    assert attendees == [("Соломичева Светлана Викторовна", "solomicheva@turbo-don.ru")]


@pytest.mark.asyncio
async def test_resolve_attendees_loads_email_from_user() -> None:
    user_id = uuid.uuid4()
    participant = SimpleNamespace(
        user_id=user_id,
        person_fio="Иванов Иван Иванович",
        person_email=None,
        sort_order=0,
    )
    meeting = _meeting(participants=[participant])

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(
        return_value=SimpleNamespace(
            full_name="Иванов Иван Иванович",
            email="ivanov@turbo-don.ru",
        )
    )

    with patch(
        "app.services.scheduled_meeting_person._invitable_email_for_user",
        AsyncMock(return_value="ivanov@turbo-don.ru"),
    ):
        attendees = await resolve_attendees(mock_db, meeting)

    assert attendees == [("Иванов Иван Иванович", "ivanov@turbo-don.ru")]


@pytest.mark.asyncio
async def test_resolve_attendees_falls_back_to_outlook_for_placeholder_email() -> None:
    user_id = uuid.uuid4()
    participant = SimpleNamespace(
        user_id=user_id,
        person_fio="Соломичева Светлана Викторовна",
        person_email="1c+test@enterprise.sync.local",
        sort_order=0,
    )
    meeting = _meeting(participants=[participant])
    mock_db = AsyncMock()
    mock_db.get = AsyncMock(
        return_value=SimpleNamespace(
            full_name="Соломичева Светлана Викторовна",
            email="1c+test@enterprise.sync.local",
        )
    )

    with patch(
        "app.services.scheduled_meeting_person._invitable_email_for_user",
        AsyncMock(return_value="solom@turbo-don.ru"),
    ):
        attendees = await resolve_attendees(mock_db, meeting)

    assert attendees == [("Соломичева Светлана Викторовна", "solom@turbo-don.ru")]


@pytest.mark.asyncio
async def test_resolve_attendees_rejects_missing_email() -> None:
    participant = SimpleNamespace(
        user_id=None,
        person_fio="Без email",
        person_email=None,
        id="participant-1",
        sort_order=0,
    )
    meeting = _meeting(participants=[participant])

    with pytest.raises(ScheduledMeetingOutlookError, match="e-mail"):
        await resolve_attendees(AsyncMock(), meeting)


@patch("app.services.scheduled_meeting_outlook.lookup_fios_by_position_title")
def test_emails_for_removed_position_matches_db_user_in_series(mock_lookup_fios) -> None:
    mock_lookup_fios.return_value = []
    emails = _emails_for_removed_position_in_series(
        "Директор по развитию",
        series_attendees=[("Соломичева Светлана Викторовна", "director@turbo-don.ru")],
        users_by_position={
            "директор по развитию": [("Соломичева Светлана Викторовна", "director@turbo-don.ru")],
        },
    )
    assert emails == ["director@turbo-don.ru"]
    mock_lookup_fios.assert_not_called()


@patch("app.services.scheduled_meeting_outlook.lookup_fios_by_position_title")
def test_emails_for_removed_position_matches_fio_in_series_display_name(mock_lookup_fios) -> None:
    mock_lookup_fios.return_value = ["Кондратюк Михаела Борисовна"]
    emails = _emails_for_removed_position_in_series(
        "Ведущий менеджер по развитию",
        series_attendees=[("Кондратюк Михаела Борисовна", "kondratyuk@turbo-don.ru")],
        users_by_position={},
    )
    assert emails == ["kondratyuk@turbo-don.ru"]


@patch("app.services.scheduled_meeting_outlook.lookup_fios_by_position_title")
def test_emails_for_removed_position_skips_email_not_in_series(mock_lookup_fios) -> None:
    mock_lookup_fios.return_value = []
    emails = _emails_for_removed_position_in_series(
        "Директор по развитию",
        series_attendees=[("Другой человек", "other@turbo-don.ru")],
        users_by_position={
            "директор по развитию": [("Соломичева Светлана Викторовна", "director@turbo-don.ru")],
        },
    )
    assert emails == []
