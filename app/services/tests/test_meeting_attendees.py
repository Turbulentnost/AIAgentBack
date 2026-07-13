from app.services.meeting_attendees import (
    attendee_fio_from_detail,
    collect_attendees_from_detail,
    emails_by_fio_from_detail,
    participants_from_detail,
)


def test_collect_attendees_includes_initiator_manager_and_participants() -> None:
    detail = {
        "application": {
            "initiator": {"full_name": "Сысоева Ирина Леонидовна"},
            "manager": {"full_name": "Иванов Иван Иванович"},
            "participants": [
                {"full_name": "Петров Петр Петрович"},
                {"full_name": "Сысоева Ирина Леонидовна"},
            ],
        }
    }

    attendees = collect_attendees_from_detail(detail)

    assert attendees == [
        ("Сысоева Ирина Леонидовна", "initiator"),
        ("Иванов Иван Иванович", "manager"),
        ("Петров Петр Петрович", "participant"),
    ]
    assert attendee_fio_from_detail(detail) == [
        "Сысоева Ирина Леонидовна",
        "Иванов Иван Иванович",
        "Петров Петр Петрович",
    ]


def test_collect_attendees_deduplicates_manager_equal_to_initiator() -> None:
    detail = {
        "application": {
            "initiator": {"full_name": "Сысоева Ирина Леонидовна"},
            "manager": {"full_name": "Сысоева Ирина Леонидовна"},
            "participants": [],
        }
    }

    assert collect_attendees_from_detail(detail) == [
        ("Сысоева Ирина Леонидовна", "initiator"),
    ]


def test_emails_by_fio_from_detail_reads_cached_emails() -> None:
    detail = {
        "application": {
            "initiator": {"full_name": "A", "email": "a@turbo-don.ru"},
            "manager": {"full_name": "B", "email": "b@turbo-don.ru"},
            "participants": [{"full_name": "C", "email": "c@turbo-don.ru"}],
        }
    }

    assert emails_by_fio_from_detail(detail) == {
        "A": "a@turbo-don.ru",
        "B": "b@turbo-don.ru",
        "C": "c@turbo-don.ru",
    }


def test_participants_from_detail_returns_only_participants() -> None:
    detail = {
        "application": {
            "initiator": {"full_name": "Сысоева Ирина Леонидовна"},
            "manager": {"full_name": "Иванов Иван Иванович"},
            "participants": [
                {"full_name": "Петров Петр Петрович", "department": "УД"},
                {"full_name": "Сысоева Ирина Леонидовна"},
            ],
        }
    }

    assert participants_from_detail(detail) == [
        "Петров Петр Петрович",
        "Сысоева Ирина Леонидовна",
    ]


def test_participants_from_detail_falls_back_to_queue_names() -> None:
    detail = {
        "application": {"participants": []},
        "queue": {"participant_names": ["Иванов Иван Иванович", "Петров Петр Петрович"]},
    }

    assert participants_from_detail(detail) == [
        "Иванов Иван Иванович",
        "Петров Петр Петрович",
    ]
