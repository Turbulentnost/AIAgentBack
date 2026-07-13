from __future__ import annotations

from types import SimpleNamespace

import pytest
from exchangelib.properties import Attendee, Mailbox

from app.tools.Outlook.update_meeting_attendees import (
    all_attendee_emails,
    apply_attendee_changes,
    normalize_emails,
)


def attendee(email: str) -> Attendee:
    return Attendee(mailbox=Mailbox(email_address=email))


def test_normalize_emails_deduplicates_case_insensitive() -> None:
    assert normalize_emails(["A@co.ru", "a@co.ru", " B@co.ru "]) == ["A@co.ru", "B@co.ru"]


def test_apply_attendee_changes_adds_and_removes() -> None:
    item = SimpleNamespace(
        organizer=SimpleNamespace(email_address="postagant@turbo-don.ru"),
        required_attendees=[
            attendee("old@turbo-don.ru"),
            attendee("keep@turbo-don.ru"),
        ],
        optional_attendees=[],
        body="",
    )

    result = apply_attendee_changes(
        item,
        add=["new@turbo-don.ru"],
        remove=["old@turbo-don.ru"],
    )

    assert result["added"] == ["new@turbo-don.ru"]
    assert result["removed"] == ["old@turbo-don.ru"]
    assert all_attendee_emails(item) == [
        "keep@turbo-don.ru",
        "new@turbo-don.ru",
    ]


def test_apply_attendee_changes_skips_organizer_removal() -> None:
    item = SimpleNamespace(
        organizer=SimpleNamespace(email_address="postagant@turbo-don.ru"),
        required_attendees=[attendee("postagant@turbo-don.ru"), attendee("guest@turbo-don.ru")],
        optional_attendees=[],
        body="",
    )

    result = apply_attendee_changes(item, remove=["postagant@turbo-don.ru"])

    assert result["removed"] == []
    assert result["skipped_remove"] == ["postagant@turbo-don.ru"]
    assert all_attendee_emails(item) == [
        "postagant@turbo-don.ru",
        "guest@turbo-don.ru",
    ]


def test_apply_attendee_changes_requires_changes() -> None:
    item = SimpleNamespace(
        organizer=None,
        required_attendees=[attendee("keep@turbo-don.ru")],
        optional_attendees=[],
        body="",
    )
    with pytest.raises(ValueError, match="--add или --remove"):
        apply_attendee_changes(item)

    result = apply_attendee_changes(item, add=["keep@turbo-don.ru"])
    assert result["added"] == []
    assert result["removed"] == []
