from app.services.meeting_attendee_priority import (
    PRIORITY_DIRECTOR,
    PRIORITY_INITIATOR,
    PRIORITY_MANAGER,
    PRIORITY_PARTICIPANT,
    is_director_person,
    is_required_priority_role,
    resolve_priority_role,
    weight_for_priority_role,
)
from app.services.meeting_attendees import collect_attendees_from_detail


def test_resolve_priority_role_detects_director_by_position() -> None:
    person = {"full_name": "Иванов", "position": "Заместитель генерального директора"}
    assert resolve_priority_role("participant", person) == PRIORITY_DIRECTOR
    assert is_required_priority_role(PRIORITY_DIRECTOR) is True
    assert weight_for_priority_role(PRIORITY_DIRECTOR) == 3.0
    assert weight_for_priority_role(PRIORITY_INITIATOR) == 2.0
    assert weight_for_priority_role(PRIORITY_MANAGER) == 2.0
    assert weight_for_priority_role(PRIORITY_PARTICIPANT) == 1.0


def test_initiator_director_gets_weight_three() -> None:
    person = {"full_name": "Иванов", "position": "Коммерческий директор"}
    assert resolve_priority_role("initiator", person) == PRIORITY_INITIATOR
    assert weight_for_priority_role(PRIORITY_INITIATOR, person) == 3.0
    assert weight_for_priority_role(PRIORITY_MANAGER, person) == 3.0


def test_collect_attendees_marks_director_participant() -> None:
    detail = {
        "application": {
            "initiator": {"full_name": "A"},
            "manager": {"full_name": "B"},
            "participants": [
                {"full_name": "C", "position": "Коммерческий директор"},
                {"full_name": "D"},
            ],
        }
    }

    attendees = collect_attendees_from_detail(detail)

    assert attendees == [
        ("A", "initiator"),
        ("B", "manager"),
        ("C", "director"),
        ("D", "participant"),
    ]


def test_is_director_person_false_for_regular_participant() -> None:
    assert is_director_person({"position": "Инженер"}) is False
