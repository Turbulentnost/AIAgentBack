from __future__ import annotations

from unittest.mock import ANY, MagicMock, patch

import pytest

from app.tools.onec.create_protocol import (
    PROTOCOL_ATTENDEES_SECTION,
    build_protocol_attendees_rows,
    build_protocol_payload,
    resolve_protocol_attendee_person_keys,
)


def test_build_protocol_payload_omits_number_for_auto_numbering() -> None:
    template = {
        "Статус": "Подготовлен",
        "ВидСовещания": "Отчетное",
        "Подразделение_Type": "StandardODATA.Catalog_ПодразделенияОрганизаций",
    }

    payload = build_protocol_payload(
        template,
        comment="Тест",
        meeting_type="Отчетное",
        department_key="dept-guid",
    )

    assert "Number" not in payload
    assert payload["Подразделение_Key"] == "dept-guid"
    assert payload["ВидСовещания"] == "Отчетное"


def test_build_protocol_payload_sets_explicit_number() -> None:
    template = {"Статус": "Подготовлен", "ВидСовещания": "Отчетное"}

    payload = build_protocol_payload(
        template,
        number="НСР_001_О_001",
        comment="Тест",
    )

    assert payload["Number"] == "НСР_001_О_001"


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
            "app.tools.onec.create_protocol.load_persons_for_keys",
            return_value={},
        ),
    ):
        resolved = resolve_protocol_attendee_person_keys(session, None, [user_ref])

    assert resolved == [person_ref]


def test_build_protocol_payload_sets_creation_date_from_now() -> None:
    template = {
        "Статус": "Подготовлен",
        "ВидСовещания": "Отчетное",
        "ДатаСоздания": "2026-07-14T15:33:41",
    }

    payload = build_protocol_payload(template, comment="Тест")

    assert payload["Date"] == payload["ДатаСоздания"]
    assert payload["ДатаСоздания"] != "2026-07-14T15:33:41"


def test_build_protocol_payload_includes_topic_participants() -> None:
    template = {"Статус": "Подготовлен", "ВидСовещания": "Отчетное"}
    session = MagicMock()
    user_ref = "user-1"
    person_ref = "person-1"

    with patch(
        "app.tools.onec.create_protocol.resolve_protocol_attendee_person_keys",
        return_value=[person_ref],
    ) as resolve_mock:
        payload = build_protocol_payload(
            template,
            comment="Тест",
            topic_key="topic-guid",
            participant_ref_keys=[user_ref],
            session=session,
        )

    resolve_mock.assert_called_once_with(session, ANY, [user_ref])
    assert PROTOCOL_ATTENDEES_SECTION in payload
    assert payload[PROTOCOL_ATTENDEES_SECTION][0]["Участник_Key"] == person_ref
    assert payload[PROTOCOL_ATTENDEES_SECTION][0]["Ref_Key"] == payload["Ref_Key"]
