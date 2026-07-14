from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from exchangelib.items import SEND_TO_NONE

from app.tools.Outlook.company_calendar_sync import (
    cancel_meeting_in_company_calendar,
    company_calendar_address,
    is_company_calendar_email,
    sync_meeting_to_company_calendar,
)


def test_company_calendar_address_reads_config() -> None:
    config = SimpleNamespace(
        company_calendar="calendar@turbo-don.ru",
        mailbox="postagent@turbo-don.ru",
        email="postagent@turbo-don.ru",
    )
    assert company_calendar_address(config) == "calendar@turbo-don.ru"


def test_is_company_calendar_email() -> None:
    config = SimpleNamespace(
        company_calendar="calendar@turbo-don.ru",
        mailbox="postagent@turbo-don.ru",
        email="postagent@turbo-don.ru",
    )
    assert is_company_calendar_email("calendar@turbo-don.ru", config=config)
    assert not is_company_calendar_email("user@turbo-don.ru", config=config)


def test_sync_meeting_to_company_calendar_creates_copy() -> None:
    config = SimpleNamespace(
        company_calendar="calendar@turbo-don.ru",
        mailbox="postagent@turbo-don.ru",
        email="postagent@turbo-don.ru",
    )
    source = SimpleNamespace(
        subject="Совещание",
        body="body",
        start="2026-07-14 10:00",
        end="2026-07-14 11:00",
        location="Зал",
        required_attendees=[],
        optional_attendees=[],
        resources=[],
        recurrence=None,
    )
    saved_item = SimpleNamespace(
        id="company-id",
        changekey="company-ck",
        save=MagicMock(),
    )

    with (
        patch(
            "app.tools.Outlook.read_calendars.connect_as_owner",
            return_value=SimpleNamespace(calendar=MagicMock()),
        ),
        patch(
            "app.tools.Outlook.company_calendar_sync.get_company_calendar_item",
            return_value=None,
        ),
        patch(
            "app.tools.Outlook.company_calendar_sync.find_company_calendar_item",
            return_value=None,
        ),
        patch(
            "app.tools.Outlook.company_calendar_sync.CalendarItem",
            return_value=saved_item,
        ) as calendar_item_cls,
    ):
        result = sync_meeting_to_company_calendar(source, config=config)

    calendar_item_cls.assert_called_once()
    saved_item.save.assert_called_once_with(send_meeting_invitations=SEND_TO_NONE)
    assert result["company_calendar_synced"] is True
    assert result["company_calendar_item_id"] == "company-id"


def test_cancel_meeting_in_company_calendar_cancels_existing_copy() -> None:
    config = SimpleNamespace(
        company_calendar="calendar@turbo-don.ru",
        mailbox="postagent@turbo-don.ru",
        email="postagent@turbo-don.ru",
    )
    existing = SimpleNamespace(
        id="company-id",
        changekey="company-ck",
        is_cancelled=False,
        delete=MagicMock(),
    )

    with patch(
        "app.tools.Outlook.company_calendar_sync.get_company_calendar_item",
        return_value=existing,
    ):
        result = cancel_meeting_in_company_calendar(
            config=config,
            company_item_id="company-id",
            company_changekey="company-ck",
        )

    existing.delete.assert_called_once()
    assert result["company_calendar_synced"] is True
