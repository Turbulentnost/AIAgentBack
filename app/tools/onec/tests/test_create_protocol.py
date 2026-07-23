from __future__ import annotations

from unittest.mock import ANY, MagicMock, patch

import pytest

from app.services.meeting_protocol_fields import MEMO_BASIS_TYPE
from app.tools.onec.create_protocol import (
    PROTOCOL_ATTENDEES_SECTION,
    build_protocol_attendees_rows,
    build_protocol_payload,
    fetch_previous_protocol_by_topic,
    resolve_protocol_attendee_person_keys,
)
from app.tools.onec.connection import CONFIG


def test_build_protocol_payload_omits_number_for_auto_numbering() -> None:
    payload = build_protocol_payload(
        comment="Тест",
        meeting_type="Отчетное",
        department_key="dept-guid",
        room_key="room-guid",
        basis_key="memo-guid",
        basis_type=MEMO_BASIS_TYPE,
    )

    assert "Number" not in payload
    assert payload["Подразделение_Key"] == "dept-guid"
    assert payload["Кабинет_Key"] == "room-guid"
    assert payload["ДокументОснование"] == "memo-guid"
    assert payload["ДокументОснование_Type"] == MEMO_BASIS_TYPE
    assert payload["ВидСовещания"] == "Отчетное"


def test_build_protocol_payload_sets_explicit_number() -> None:
    payload = build_protocol_payload(
        number="НСР_001_О_001",
        comment="Тест",
    )

    assert payload["Number"] == "НСР_001_О_001"


def test_build_protocol_payload_omits_next_meeting_date_when_not_provided() -> None:
    payload = build_protocol_payload(comment="Единоразовое")

    assert "ДатаСледующегоСовещания" not in payload


def test_build_protocol_payload_sets_next_meeting_date_for_series() -> None:
    payload = build_protocol_payload(
        comment="Серия",
        next_meeting_date="2026-08-05T00:00:00",
    )

    assert payload["ДатаСледующегоСовещания"] == "2026-08-05T00:00:00"


def test_build_protocol_attendees_rows() -> None:
    document_ref = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    rows = build_protocol_attendees_rows(
        ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"],
        document_ref_key=document_ref,
    )

    assert len(rows) == 2
    assert rows[0]["Ref_Key"] == document_ref
    assert rows[0]["LineNumber"] == "1"
    assert rows[0]["Участник_Key"] == "11111111-1111-1111-1111-111111111111"
    assert rows[1]["LineNumber"] == "2"
    assert "Участник_Type" not in rows[0]


def test_resolve_protocol_attendee_person_keys_from_users() -> None:
    session = MagicMock()
    user_ref = "user-1"
    person_ref = "person-1"

    with (
        patch(
            "app.tools.onec.get_porucheniya.load_users_for_keys",
            return_value={
                user_ref: {"Ref_Key": user_ref, "ФизическоеЛицо_Key": person_ref},
            },
        ),
        patch(
            "app.tools.onec.lookup_user_ref.resolve_person_keys_by_refs",
            return_value=[person_ref],
        ),
    ):
        resolved = resolve_protocol_attendee_person_keys(session, None, [user_ref])

    assert resolved == [person_ref]


def test_build_protocol_payload_sets_creation_date_from_now() -> None:
    payload = build_protocol_payload(comment="Тест")

    assert payload["Date"] == payload["ДатаСоздания"]


def test_build_protocol_payload_includes_topic_participants() -> None:
    session = MagicMock()
    user_ref = "user-1"
    person_ref = "person-1"

    with patch(
        "app.tools.onec.create_protocol.resolve_protocol_attendee_person_keys",
        return_value=[person_ref],
    ) as resolve_mock:
        payload = build_protocol_payload(
            comment="Тест",
            topic_key="topic-guid",
            participant_ref_keys=[user_ref],
            session=session,
        )

    resolve_mock.assert_called_once_with(session, ANY, [user_ref])
    assert PROTOCOL_ATTENDEES_SECTION in payload
    assert payload[PROTOCOL_ATTENDEES_SECTION][0]["Участник_Key"] == person_ref
    assert payload[PROTOCOL_ATTENDEES_SECTION][0]["Ref_Key"] == payload["Ref_Key"]


def test_fetch_previous_protocol_by_topic_builds_filter() -> None:
    session = MagicMock()
    response = MagicMock(ok=True)
    response.json.return_value = {"value": [{"Ref_Key": "prev-1", "Number": "P_001"}]}
    session.get.return_value = response

    row = fetch_previous_protocol_by_topic(
        session,
        CONFIG,
        "topic-guid",
        before=__import__("datetime").datetime(2026, 7, 22, 14, 0, 0),
    )

    assert row is not None
    assert row["Ref_Key"] == "prev-1"
    called_url = session.get.call_args.args[0]
    assert "topic-guid" in called_url
    assert "2026-07-22T14%3A00%3A00" in called_url or "2026-07-22T14:00:00" in called_url
