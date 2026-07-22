from __future__ import annotations

from app.tools.onec.create_protocol import build_protocol_payload


def test_build_protocol_payload_omits_number_for_auto_numbering() -> None:
    template = {
        "Статус": "Подготовлен",
        "ВидСовещания": "Отчетное",
        "Подразделение_Type": "StandardODATA.Catalog_ПодразделенияОрганизаций",
    }

    payload = build_protocol_payload(
        template,
        comment="Тест",
        topic_key="topic-guid",
        meeting_type="Отчетное",
        department_key="dept-guid",
    )

    assert "Number" not in payload
    assert payload["ТемаСовещания_Key"] == "topic-guid"
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
