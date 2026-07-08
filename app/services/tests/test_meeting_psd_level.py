from app.services.meeting_attendees import emails_by_fio_from_detail
from app.services.meeting_psd_level import (
    PSD_LEVEL_PARTICIPANT_EMAIL,
    PSD_LEVEL_PARTICIPANT_FIO,
    append_psd_level_participant_names,
    append_psd_level_participants,
    is_psd_level_header,
    is_psd_level_value,
)
from app.agents.meeting_agent.backend import _extract_participant_fio


def test_is_psd_level_value() -> None:
    assert is_psd_level_value("Да")
    assert is_psd_level_value("нет") is False


def test_append_psd_level_participant_names() -> None:
    names = append_psd_level_participant_names(["A"], psd_level=True)
    assert names == ["A", PSD_LEVEL_PARTICIPANT_FIO]
    assert append_psd_level_participant_names(names, psd_level=True) == names


def test_extract_participant_fio_adds_psd_participant() -> None:
    document = {
        "header": {"НаУровнеПСД": "Да"},
        "participants": [{"ФИО": "Иванов Иван Иванович"}],
    }
    assert _extract_participant_fio(document) == [
        "Иванов Иван Иванович",
        PSD_LEVEL_PARTICIPANT_FIO,
    ]


def test_psd_level_participant_has_known_email() -> None:
    from app.services.meeting_psd_level import psd_level_participant_dict

    participant = psd_level_participant_dict()
    assert participant["email"] == PSD_LEVEL_PARTICIPANT_EMAIL
    assert participant["full_name"] == PSD_LEVEL_PARTICIPANT_FIO


def test_emails_by_fio_from_detail_includes_psd_email() -> None:
    detail = {
        "application": {
            "participants": [
                {
                    "full_name": PSD_LEVEL_PARTICIPANT_FIO,
                    "email": PSD_LEVEL_PARTICIPANT_EMAIL,
                }
            ],
        }
    }
    assert emails_by_fio_from_detail(detail)[PSD_LEVEL_PARTICIPANT_FIO] == PSD_LEVEL_PARTICIPANT_EMAIL
