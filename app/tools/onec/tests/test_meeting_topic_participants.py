from app.tools.onec.meeting_topic_participants import (
    PARTICIPANT_KEY_FIELD,
    TOPIC_KEY_FIELD,
    build_participant_record_payload,
    extract_participant_keys,
    normalize_participant_row,
)


def test_normalize_participant_row_resolves_fio_from_person() -> None:
    row = {
        TOPIC_KEY_FIELD: "topic-1",
        PARTICIPANT_KEY_FIELD: "person-1",
    }
    persons = {
        "person-1": {
            "Ref_Key": "person-1",
            "Description": "Соломичева Светлана Викторовна",
        }
    }

    item = normalize_participant_row(row, users={}, persons=persons)

    assert item["participant_ref_key"] == "person-1"
    assert item["fio"] == "Соломичева Светлана Викторовна"
    assert item["topic_ref_key"] == "topic-1"


def test_normalize_participant_row_resolves_fio_from_user_legacy() -> None:
    row = {
        TOPIC_KEY_FIELD: "topic-1",
        PARTICIPANT_KEY_FIELD: "user-1",
    }
    users = {
        "user-1": {
            "Ref_Key": "user-1",
            "Description": "Соломичева Светлана Викторовна",
            "ФизическоеЛицо_Key": "person-1",
        }
    }
    persons = {
        "person-1": {
            "Ref_Key": "person-1",
            "Description": "Соломичева Светлана Викторовна",
        }
    }

    item = normalize_participant_row(row, users=users, persons=persons)

    assert item["participant_ref_key"] == "user-1"
    assert item["fio"] == "Соломичева Светлана Викторовна"
    assert item["topic_ref_key"] == "topic-1"


def test_build_participant_record_payload_uses_physical_person_type() -> None:
    payload = build_participant_record_payload(
        topic_ref_key="topic-1",
        participant_ref_key="person-1",
    )

    assert payload[TOPIC_KEY_FIELD] == "topic-1"
    assert payload[PARTICIPANT_KEY_FIELD] == "person-1"
    assert payload["УчастникСовещания_Type"] == "StandardODATA.Catalog_ФизическиеЛица"


def test_extract_participant_keys_skips_empty() -> None:
    assert extract_participant_keys([{PARTICIPANT_KEY_FIELD: "00000000-0000-0000-0000-000000000000"}]) == []
