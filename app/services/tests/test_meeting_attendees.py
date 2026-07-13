from __future__ import annotations

from types import SimpleNamespace

from app.services.meeting_attendees import (
    collect_attendees_from_registry_entry,
    participants_from_detail,
    registry_attendee_sync_diff,
)
from app.services.meeting_attendee_priority import PRIORITY_INITIATOR, PRIORITY_MANAGER, PRIORITY_PARTICIPANT


def test_registry_attendee_sync_diff_detects_removed_participant() -> None:
    entry = SimpleNamespace(
        payload={
            "attendees": ["ivanov@turbo-don.ru"],
            "sent_payload": {
                "attendees": ["ivanov@turbo-don.ru", "petrov@turbo-don.ru"],
            },
        }
    )

    add, remove = registry_attendee_sync_diff(entry)

    assert add == []
    assert remove == ["petrov@turbo-don.ru"]


def test_registry_attendee_sync_diff_returns_empty_without_sent_payload() -> None:
    entry = SimpleNamespace(payload={"attendees": ["ivanov@turbo-don.ru"]})

    add, remove = registry_attendee_sync_diff(entry)

    assert add == []
    assert remove == []


def test_participants_from_detail_includes_initiator_manager_and_participants() -> None:
    detail = {
        "application": {
            "initiator": {"full_name": "Комарькова Анастасия Эдуардовна"},
            "manager": {"full_name": "Соломичева Светлана Викторовна"},
            "participants": [
                {"full_name": "Мангасарян Давид Каренович"},
                {"full_name": "Азарова Анна Александровна"},
            ],
        },
    }

    assert participants_from_detail(detail) == [
        "Комарькова Анастасия Эдуардовна",
        "Соломичева Светлана Викторовна",
        "Мангасарян Давид Каренович",
        "Азарова Анна Александровна",
    ]


def test_collect_attendees_from_registry_entry_uses_entry_participants_only() -> None:
    entry = SimpleNamespace(
        participants=[
            "Соломичева Светлана Викторовна",
            "Кондратюк Михаела Борисовна",
        ],
        initiator_name="Комарькова Анастасия Эдуардовна",
        manager_name="Соломичева Светлана Викторовна",
    )

    attendees = collect_attendees_from_registry_entry(entry)

    assert attendees == [
        ("Соломичева Светлана Викторовна", PRIORITY_MANAGER),
        ("Кондратюк Михаела Борисовна", PRIORITY_PARTICIPANT),
    ]
    assert all(name != "Комарькова Анастасия Эдуардовна" for name, _role in attendees)
