import uuid

import pytest

from app.tools.onec.create_meeting_topic import (
    MEETING_TYPES,
    build_meeting_topic_payload,
    copy_template_fields,
    default_closed_date,
    meeting_time,
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


def test_build_meeting_topic_payload_uses_template() -> None:
    template = {
        "Ref_Key": "c7781365-f149-11f0-977f-6cb31113810c",
        "Code": "000009459",
        "Description": "Технический совет",
        "ВидСовещания": "Отчетное",
        "Подразделение_Key": "4668a58b-6eb1-11e2-afce-001e67112509",
        "Кабинет_Key": "35ccfb35-ad89-11f0-9720-6cb31113810e",
        "ДатаЗакрытияТемы": "2026-12-31T00:00:00",
        "ТемаКругаУправления": True,
    }
    payload = build_meeting_topic_payload(
        description="Новая тема",
        manager_ref="5b2e1e74-a805-11eb-85c6-ac1f6b05524d",
        meeting_type="Внеплановое",
        template=template,
    )

    assert payload["Description"] == "Новая тема"
    assert payload["ВидСовещания"] == "Внеплановое"
    assert payload["Подразделение_Key"] == template["Подразделение_Key"]
    assert payload["Кабинет_Key"] == template["Кабинет_Key"]
    assert payload["ТемаКругаУправления"] is True
    assert payload["ВидСовещания"] == "Внеплановое"


def test_copy_template_fields_skips_service_fields() -> None:
    copied = copy_template_fields(
        {
            "Ref_Key": "abc",
            "Code": "000001",
            "Description": "Old",
            "Подразделение_Key": "dept-1",
            "Predefined": False,
        }
    )

    assert "Ref_Key" not in copied
    assert "Code" not in copied
    assert "Description" not in copied
    assert copied["Подразделение_Key"] == "dept-1"


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
