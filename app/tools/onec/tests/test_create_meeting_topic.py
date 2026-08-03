import uuid
from unittest.mock import patch

import pytest

from app.tools.onec.create_meeting_topic import (
    MEETING_TYPES,
    build_meeting_topic_payload,
    build_skip_result,
    default_closed_date,
    meeting_time,
    merge_topic_participant_fios,
    require_topic_participant_fios,
    resolve_topic_participants,
)


def test_build_meeting_topic_payload_required_fields() -> None:
    payload = build_meeting_topic_payload(
        description="Технический совет",
        manager_ref="5b2e1e74-a805-11eb-85c6-ac1f6b05524d",
        meeting_type="Отчетное",
        closed_date="2026-12-31T00:00:00",
        department_key="4668a58b-6eb1-11e2-afce-001e67112509",
        room_key="35ccfb35-ad89-11f0-9720-6cb31113810e",
        start_time=meeting_time(13, 10),
        end_time=meeting_time(14, 0),
        is_management_circle_topic=True,
    )

    assert payload["Description"] == "Технический совет"
    assert payload["ВидСовещания"] == "Отчетное"
    assert payload["Руководитель_Key"] == "5b2e1e74-a805-11eb-85c6-ac1f6b05524d"
    assert payload["Проверяющий_Key"] == "5b2e1e74-a805-11eb-85c6-ac1f6b05524d"
    assert payload["ДатаЗакрытияТемы"] == "2026-12-31T00:00:00"
    assert payload["Подразделение_Key"] == "4668a58b-6eb1-11e2-afce-001e67112509"
    assert payload["Кабинет_Key"] == "35ccfb35-ad89-11f0-9720-6cb31113810e"
    assert payload["ТемаКругаУправления"] is True
    assert payload["DeletionMark"] is False
    uuid.UUID(payload["Ref_Key"])


def test_build_meeting_topic_payload_validates_meeting_type() -> None:
    with pytest.raises(ValueError, match="Вид совещания"):
        build_meeting_topic_payload(
            description="Test",
            manager_ref="5b2e1e74-a805-11eb-85c6-ac1f6b05524d",
            meeting_type="Unknown",
        )


def test_default_closed_date_end_of_year() -> None:
    assert default_closed_date(end_of_year=True).endswith("-12-31T00:00:00")


def test_meeting_types_contains_expected_values() -> None:
    assert "Отчетное" in MEETING_TYPES
    assert "Плановое" in MEETING_TYPES


def test_build_skip_result() -> None:
    result = build_skip_result(
        similar_topic={
            "code": "000009370",
            "description": "Еженедельное совещание с главным метрологом",
            "similarity_score": 0.95,
        },
        dry_run=False,
        manager_fio="Мегрелишвили Михаил Эмзарович",
        reviewer_fio="Мегрелишвили Михаил Эмзарович",
    )

    assert result["skipped"] is True
    assert result["created"] is False
    assert result["skip_reason"] == "similar_topic_exists"
    assert "000009370" in result["message"]


def test_resolve_topic_participants_adds_initiator_manager_and_explicit_fios() -> None:
    class Session:
        pass

    with (
        patch(
            "app.tools.onec.create_meeting_topic.resolve_person_keys_by_refs",
            side_effect=lambda _session, refs, **_kwargs: {
                "user-0": ["person-0"],
                "user-1": ["person-1"],
                "user-0,user-1": ["person-0", "person-1"],
            }[",".join(refs)],
        ),
        patch(
            "app.tools.onec.create_meeting_topic.resolve_participant_refs_by_fio",
            return_value=[{"participant_ref_key": "person-2", "fio": "Хозуян Иван Владимирович"}],
        ),
    ):
        resolved = resolve_topic_participants(
            Session(),
            None,
            manager_ref="user-1",
            initiator_ref="user-0",
            participant_fios=["Хозуян Иван Владимирович"],
        )

    assert [item["participant_ref_key"] for item in resolved] == [
        "person-0",
        "person-1",
        "person-2",
    ]


def test_require_topic_participant_fios_rejects_empty() -> None:
    with pytest.raises(ValueError, match="Укажите участников"):
        require_topic_participant_fios([])


def test_require_topic_participant_fios_normalizes() -> None:
    assert require_topic_participant_fios(["  Иванов ", "Иванов"]) == ["Иванов"]


def test_merge_topic_participant_fios_includes_initiator_and_manager() -> None:
    merged = merge_topic_participant_fios(
        ["Мангасарян Давид Каренович"],
        manager_fio="Соломичева Светлана Викторовна",
        initiator_fio="Комарькова Анастасия Эдуардовна",
    )

    assert merged == [
        "Комарькова Анастасия Эдуардовна",
        "Соломичева Светлана Викторовна",
        "Мангасарян Давид Каренович",
    ]
