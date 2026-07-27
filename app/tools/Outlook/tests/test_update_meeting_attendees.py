from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from exchangelib.properties import Attendee, Mailbox

from app.tools.Outlook.update_meeting_attendees import (
    all_attendee_emails,
    apply_attendee_changes,
    normalize_emails,
    update_meeting_attendees_item,
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


def test_update_meeting_attendees_item_occurrence_scope_removes_from_occurrence() -> None:
    occurrence = SimpleNamespace(
        type="Occurrence",
        id="occ-1",
        subject="Серия",
        is_cancelled=False,
        organizer=SimpleNamespace(email_address="postagant@turbo-don.ru"),
        required_attendees=[attendee("keep@turbo-don.ru"), attendee("old@turbo-don.ru")],
        optional_attendees=[],
        body="",
        account=SimpleNamespace(),
    )
    occurrence.save = MagicMock()

    with patch(
        "app.tools.Outlook.update_meeting_attendees.send_attendee_update_notifications",
        return_value={
            "notified_existing": ["keep@turbo-don.ru"],
            "notified_new": [],
            "notified_removed": ["old@turbo-don.ru"],
            "notification_errors": [],
        },
    ):
        result = update_meeting_attendees_item(
            occurrence,
            remove=["old@turbo-don.ru"],
            attendees_scope="occurrence",
        )

    occurrence.save.assert_called_once()
    assert result["attendees_scope"] == "occurrence"
    assert result["target_kind"] == "series_occurrence"
    assert result["removed"] == ["old@turbo-don.ru"]
    assert all_attendee_emails(occurrence) == ["keep@turbo-don.ru"]


def test_update_meeting_attendees_item_series_scope_removes_from_master() -> None:
    master = SimpleNamespace(
        type="RecurringMaster",
        id="master-1",
        subject="Серия",
        is_cancelled=False,
        organizer=SimpleNamespace(email_address="postagant@turbo-don.ru"),
        required_attendees=[attendee("keep@turbo-don.ru"), attendee("old@turbo-don.ru")],
        optional_attendees=[],
        body="",
        account=SimpleNamespace(),
    )
    master.save = MagicMock()
    master.refresh = MagicMock()
    occurrence = SimpleNamespace(
        type="Occurrence",
        id="occ-1",
        subject="Серия",
        is_cancelled=False,
        recurring_master=MagicMock(return_value=master),
    )

    with patch(
        "app.tools.Outlook.update_meeting_attendees.send_attendee_update_notifications",
        return_value={
            "notified_existing": ["keep@turbo-don.ru"],
            "notified_new": [],
            "notified_removed": ["old@turbo-don.ru"],
            "notification_errors": [],
        },
    ):
        result = update_meeting_attendees_item(
            occurrence,
            remove=["old@turbo-don.ru"],
            attendees_scope="series",
        )

    master.refresh.assert_called_once()
    master.save.assert_called_once()
    occurrence_save = getattr(occurrence, "save", None)
    if occurrence_save is not None:
        occurrence_save.assert_not_called()
    assert result["attendees_scope"] == "series"
    assert result["target_kind"] == "series_master"
    assert result["target_id"] == "master-1"
    assert result["removed"] == ["old@turbo-don.ru"]
    assert all_attendee_emails(master) == ["keep@turbo-don.ru"]


def test_update_meeting_attendees_item_remove_uses_roster_body() -> None:
    occurrence = SimpleNamespace(
        type="Occurrence",
        id="occ-1",
        subject="Серия",
        is_cancelled=False,
        organizer=SimpleNamespace(email_address="postagant@turbo-don.ru"),
        required_attendees=[attendee("keep@turbo-don.ru"), attendee("old@turbo-don.ru")],
        optional_attendees=[],
        body="",
        account=SimpleNamespace(),
    )
    occurrence.save = MagicMock()

    with (
        patch(
            "app.tools.Outlook.update_meeting_attendees.build_meeting_roster_calendar_body",
            return_value="<p>roster</p>",
        ) as build_roster,
        patch(
            "app.tools.Outlook.update_meeting_attendees.send_attendee_update_notifications",
            return_value={
                "notified_existing": ["keep@turbo-don.ru"],
                "notified_new": [],
                "notified_removed": ["old@turbo-don.ru"],
                "notification_errors": [],
            },
        ),
    ):
        update_meeting_attendees_item(
            occurrence,
            remove=["old@turbo-don.ru"],
            attendees_scope="occurrence",
        )

    build_roster.assert_called_once()
    assert str(occurrence.body) == "<p>roster</p>"


def test_update_meeting_attendees_item_occurrence_scope_adds_to_occurrence() -> None:
    occurrence = SimpleNamespace(
        type="Occurrence",
        id="occ-1",
        subject="Серия",
        is_cancelled=False,
        organizer=SimpleNamespace(email_address="postagant@turbo-don.ru"),
        required_attendees=[attendee("keep@turbo-don.ru")],
        optional_attendees=[],
        body="",
        account=SimpleNamespace(),
    )
    occurrence.save = MagicMock()

    with (
        patch(
            "app.tools.Outlook.update_meeting_attendees.build_meeting_roster_calendar_body",
            return_value="<p>invite</p>",
        ) as build_invite,
        patch(
            "app.tools.Outlook.update_meeting_attendees.send_attendee_update_notifications",
            return_value={
                "notified_existing": ["keep@turbo-don.ru"],
                "notified_new": ["new@turbo-don.ru"],
                "notified_removed": [],
                "notification_errors": [],
            },
        ),
    ):
        result = update_meeting_attendees_item(
            occurrence,
            add=["new@turbo-don.ru"],
            attendees_scope="occurrence",
        )

    occurrence.save.assert_called_once()
    build_invite.assert_called_once()
    assert result["attendees_scope"] == "occurrence"
    assert result["target_kind"] == "series_occurrence"
    assert result["added"] == ["new@turbo-don.ru"]
    assert all_attendee_emails(occurrence) == ["keep@turbo-don.ru", "new@turbo-don.ru"]


def test_update_meeting_attendees_item_series_scope_adds_to_master() -> None:
    master = SimpleNamespace(
        type="RecurringMaster",
        id="master-1",
        subject="Серия",
        is_cancelled=False,
        organizer=SimpleNamespace(email_address="postagant@turbo-don.ru"),
        required_attendees=[attendee("keep@turbo-don.ru")],
        optional_attendees=[],
        body="",
        account=SimpleNamespace(),
    )
    master.save = MagicMock()
    master.refresh = MagicMock()
    occurrence = SimpleNamespace(
        type="Occurrence",
        id="occ-1",
        subject="Серия",
        is_cancelled=False,
        recurring_master=MagicMock(return_value=master),
    )

    with (
        patch(
            "app.tools.Outlook.update_meeting_attendees.build_meeting_roster_calendar_body",
            return_value="<p>invite</p>",
        ) as build_invite,
        patch(
            "app.tools.Outlook.update_meeting_attendees.send_attendee_update_notifications",
            return_value={
                "notified_existing": ["keep@turbo-don.ru"],
                "notified_new": ["new@turbo-don.ru"],
                "notified_removed": [],
                "notification_errors": [],
            },
        ),
    ):
        result = update_meeting_attendees_item(
            occurrence,
            add=["new@turbo-don.ru"],
            attendees_scope="series",
        )

    master.refresh.assert_called_once()
    master.save.assert_called_once()
    save_kwargs = master.save.call_args.kwargs
    assert save_kwargs["update_fields"] == ["required_attendees", "optional_attendees", "body"]
    build_invite.assert_called_once()
    assert result["attendees_scope"] == "series"
    assert result["target_kind"] == "series_master"
    assert result["target_id"] == "master-1"
    assert result["added"] == ["new@turbo-don.ru"]
    assert result["notified_new"] == ["new@turbo-don.ru"]
    assert all_attendee_emails(master) == ["keep@turbo-don.ru", "new@turbo-don.ru"]


def test_update_meeting_attendees_item_uses_custom_calendar_invite_body() -> None:
    master = SimpleNamespace(
        type="RecurringMaster",
        id="master-1",
        subject="Серия",
        is_cancelled=False,
        organizer=SimpleNamespace(email_address="postagant@turbo-don.ru"),
        required_attendees=[attendee("keep@turbo-don.ru")],
        optional_attendees=[],
        body="",
        account=SimpleNamespace(),
    )
    master.save = MagicMock()

    custom_body = (
        "Иванов Иван Иванович <ivanov@turbo-don.ru>;\n"
        "Попов Павел Павлович <popov@turbo-don.ru>\n\n"
        "Совещание запланировано ИИ-агентом по планированию совещаний"
    )

    with (
        patch(
            "app.tools.Outlook.update_meeting_attendees.build_meeting_roster_calendar_body",
        ) as build_invite,
        patch(
            "app.tools.Outlook.update_meeting_attendees.send_attendee_update_notifications",
            return_value={
                "notified_existing": [],
                "notified_new": ["new@turbo-don.ru"],
                "notified_removed": [],
                "notification_errors": [],
            },
        ),
    ):
        result = update_meeting_attendees_item(
            master,
            add=["new@turbo-don.ru"],
            attendees_scope="series",
            calendar_invite_body=custom_body,
        )

    master.save.assert_called_once()
    build_invite.assert_not_called()
    assert "Иванов Иван Иванович" in str(master.body)
    assert result["added"] == ["new@turbo-don.ru"]
