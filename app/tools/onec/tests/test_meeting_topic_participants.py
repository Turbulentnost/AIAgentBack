from unittest.mock import MagicMock, patch

from app.tools.onec.meeting_topic_participants import (
    PARTICIPANT_KEY_FIELD,
    TOPIC_KEY_FIELD,
    build_participant_record_payload,
    extract_participant_keys,
    merge_participants_into_topic,
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


def test_merge_participants_into_topic_adds_only_missing_found_in_1c() -> None:
    session = MagicMock()
    config = MagicMock()

    with (
        patch(
            "app.tools.onec.meeting_topic_participants.collect_existing_participant_keys",
            return_value={"person-existing"},
        ),
        patch(
            "app.tools.onec.meeting_topic_participants.resolve_participant_refs_by_fio_with_missing",
            return_value=(
                [
                    {"participant_ref_key": "person-existing", "fio": "Уже Есть"},
                    {"participant_ref_key": "person-new", "fio": "Новый Участник"},
                ],
                ["Нет В 1С"],
            ),
        ) as resolve_mock,
        patch(
            "app.tools.onec.meeting_topic_participants.add_meeting_topic_participants",
            return_value=[
                {"participant_ref_key": "person-new", "fio": "Новый Участник"},
            ],
        ) as add_mock,
    ):
        result = merge_participants_into_topic(
            session,
            config,
            topic_ref_key="topic-1",
            participant_fios=["Уже Есть", "Новый Участник", "Нет В 1С"],
        )

    resolve_mock.assert_called_once()
    assert resolve_mock.call_args.kwargs["skip_missing"] is True
    add_mock.assert_called_once_with(
        session,
        config,
        topic_ref_key="topic-1",
        participant_refs=[
            {"participant_ref_key": "person-new", "fio": "Новый Участник"},
        ],
        dry_run=False,
    )
    assert result["added_count"] == 1
    assert result["added"][0]["fio"] == "Новый Участник"
    assert len(result["skipped_already_in_topic"]) == 1
    assert result["not_found_in_1c"] == [{"fio": "Нет В 1С"}]


def test_merge_participants_into_topic_noop_when_all_present() -> None:
    session = MagicMock()
    config = MagicMock()

    with (
        patch(
            "app.tools.onec.meeting_topic_participants.collect_existing_participant_keys",
            return_value={"person-1"},
        ),
        patch(
            "app.tools.onec.meeting_topic_participants.resolve_participant_refs_by_fio_with_missing",
            return_value=([{"participant_ref_key": "person-1", "fio": "Иванов"}], []),
        ),
        patch(
            "app.tools.onec.meeting_topic_participants.add_meeting_topic_participants",
        ) as add_mock,
    ):
        result = merge_participants_into_topic(
            session,
            config,
            topic_ref_key="topic-1",
            participant_fios=["Иванов"],
        )

    add_mock.assert_not_called()
    assert result["added_count"] == 0
    assert result["not_found_in_1c"] == []
