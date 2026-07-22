"""Тесты полей list API для табличного вида «Таняфикация»."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from agent_pochta.api.app import _row_to_list_dict
from agent_pochta.api import list_table_fields
from agent_pochta.api.list_table_fields import (
    default_responsible_label,
    operator_review_state,
    row_to_table_fields,
)
from agent_pochta.db.models import EmailMessageRow


def _sample_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<document>
  <organization>НП</organization>
  <theme>Тест</theme>
  <direction>ПР</direction>
  <partner>ООО «Газпром»</partner>
  <mail_datetime>2026-07-21T10:30:00</mail_datetime>
  <process>исполнение</process>
</document>"""


def _make_row(**overrides) -> EmailMessageRow:
    payload = {
        "message_id": "<table@test>",
        "xml_document": _sample_xml(),
        "attachments": [{"filename": "scan.pdf", "mime_type": "application/pdf"}],
    }
    payload.update(overrides.get("payload_extra") or {})
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<table@test>",
        received_at=datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="client@example.ru",
        sender_name="Client",
        department_id="00-000076",
        department_name="Отдел договорной работы",
        erp_document_number="КБ00-000028",
        attachments_count=1,
        raw_payload_json=json.dumps(payload, ensure_ascii=False),
    )
    for key, value in overrides.items():
        if key != "payload_extra":
            setattr(row, key, value)
    return row


def test_row_to_table_fields_from_xml():
    row = _make_row()
    fields = row_to_table_fields(row)
    assert fields["organization"] == "НП"
    assert "Турбулентность" in fields["organization_name"]
    assert fields["direction"] == "ПР"
    assert fields["payer_direction_label"] == "ООО НПО «Турбулентность-ДОН» пр-во1"
    assert fields["mail_date"] == "2026-07-21T11:00:00"
    assert fields["access_label"] == "Общий"
    assert fields["responsible_label"] == "Донченко Вера И."
    assert fields["attachments_summary"] == [{"index": 0, "filename": "scan.pdf"}]


def test_mail_date_ignores_sender_local_xml_datetime():
    """XML mail_datetime может содержать локальное время отправителя, не MSK."""
    row = _make_row(
        received_at=datetime(2026, 7, 22, 7, 37, 41, tzinfo=timezone.utc).replace(tzinfo=None),
        payload_extra={
            "xml_document": """<?xml version="1.0" encoding="UTF-8"?>
<document>
  <organization>НП</organization>
  <mail_datetime>2026-07-22 15:37:41</mail_datetime>
</document>"""
        },
    )
    fields = row_to_table_fields(row)
    assert fields["mail_date"] == "2026-07-22T10:37:41"


def test_operator_review_state_priority():
    assert operator_review_state({}) == "pending"
    assert operator_review_state({"operator_verified": True}) == "verified"
    assert operator_review_state({"operator_verified": True, "operator_corrected": True}) == "corrected"
    assert operator_review_state({"operator_corrected": True}) == "corrected"


def test_operator_review_state_infers_from_classification_events():
    assert operator_review_state({}, has_operator_approve=True) == "verified"
    assert operator_review_state({}, has_operator_change=True) == "corrected"
    assert operator_review_state({"operator_verified": False}, has_operator_approve=True) == "verified"
    assert (
        operator_review_state({}, has_operator_approve=True, has_operator_change=True)
        == "corrected"
    )


def test_row_to_list_dict_infers_verified_from_event_hints():
    row = _make_row()
    data = _row_to_list_dict(
        row,
        operator_event_hints={"has_operator_approve": True, "has_operator_change": False},
    )
    assert data["operator_review_state"] == "verified"
    assert data["operator_verified"] is False


def test_row_to_list_dict_infers_corrected_from_event_hints():
    row = _make_row()
    data = _row_to_list_dict(
        row,
        operator_event_hints={"has_operator_approve": False, "has_operator_change": True},
    )
    assert data["operator_review_state"] == "corrected"


def test_row_to_list_dict_includes_table_fields():
    row = _make_row(payload_extra={"operator_verified": True, "operator_corrected": False})
    data = _row_to_list_dict(row)
    assert data["operator_review_state"] == "verified"
    assert data["organization"] == "НП"
    assert data["payer_direction_label"]
    assert data["attachments_summary"][0]["filename"] == "scan.pdf"
    assert data["erp_document_number"] == "КБ00-000028"


def test_row_to_list_dict_corrected_state():
    row = _make_row(payload_extra={"operator_verified": True, "operator_corrected": True})
    data = _row_to_list_dict(row)
    assert data["operator_review_state"] == "corrected"


def test_default_responsible_label_skips_ai_placeholder(monkeypatch):
    monkeypatch.setattr(
        list_table_fields,
        "load_incoming_defaults",
        lambda: {"Ответственный_Key": "ai-user-guid"},
    )
    monkeypatch.setattr(
        list_table_fields,
        "load_defaults_display",
        lambda: {
            "responsible_labels": {
                "ai-user-guid": "ИИ 1С",
                "human-guid": "Донченко Вера И.",
            },
            "default_responsible_label": "Донченко Вера И.",
        },
    )
    assert default_responsible_label() == "Донченко Вера И."
