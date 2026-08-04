"""Тесты извлечения контрагентов из писем."""

from __future__ import annotations

from agent_pochta.services.contractor_seed import (
    extract_contractors_from_messages,
    partner_from_payload,
    to_contractor,
)


def _xml(partner: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<document>"
        f"<partner>{partner}</partner>"
        "<organization>НП</organization>"
        "</document>"
    )


def test_partner_from_payload_skips_dash():
    payload = {"xml_document": _xml("-")}
    assert partner_from_payload(__import__("json").dumps(payload)) is None


def test_partner_from_payload_reads_xml_partner():
    payload = {"xml_document": _xml("ООО «Ромашка»")}
    assert partner_from_payload(__import__("json").dumps(payload)) == "ООО «Ромашка»"


def test_extract_dedup_by_email_last_wins():
    rows = [
        ("client@example.com", None, __import__("json").dumps({"xml_document": _xml("Старое имя")})),
        ("client@example.com", None, __import__("json").dumps({"xml_document": _xml("Новое имя")})),
        ("other@corp.ru", "Иван", __import__("json").dumps({"xml_document": _xml("-")})),
    ]
    result = extract_contractors_from_messages(rows)
    by_email = {item.email: item for item in result}
    assert len(result) == 2
    assert by_email["client@example.com"].name == "Новое имя"
    assert by_email["other@corp.ru"].name == "corp.ru"


def test_extract_skips_invalid_partner_and_email():
    rows = [
        ("bad-email", None, __import__("json").dumps({"xml_document": _xml("ООО X")})),
        ("valid@corp.ru", None, __import__("json").dumps({"xml_document": _xml("-")})),
        ("ok@corp.ru", None, __import__("json").dumps({"xml_document": _xml("ООО OK")})),
    ]
    result = extract_contractors_from_messages(rows)
    by_email = {item.email: item for item in result}
    assert len(result) == 2
    assert by_email["valid@corp.ru"].name == "corp.ru"
    assert by_email["ok@corp.ru"].name == "ООО OK"
    contractor = to_contractor(by_email["ok@corp.ru"])
    assert contractor.contractor_id == "email:ok@corp.ru"
    assert contractor.emails == ["ok@corp.ru"]
