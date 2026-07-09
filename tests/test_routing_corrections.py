"""Тесты коррекций маршрутизации и API email-messages."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_pochta.api.app import _payload_meta, _row_to_dict, _row_to_list_dict
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.email_payload import BODY_NOT_STORED_PLACEHOLDER
from agent_pochta.routing.corrections import (
    find_correction_match,
    load_corrections,
    save_routing_correction,
)
from agent_pochta.routing.engine import RouteEngine, reset_route_engine
from agent_pochta.schemas import EmailMessage


@pytest.fixture
def corrections_file(tmp_path: Path) -> Path:
    path = tmp_path / "routing_corrections.json"
    path.write_text('{"version": "1.0", "entries": []}\n', encoding="utf-8")
    return path


def test_save_and_apply_routing_correction(corrections_file: Path):
    save_routing_correction(
        message_id="<corr@example>",
        sender_email="vendor@example.com",
        recipient="jurist@turbo-don.ru",
        subject="Акт сверки за квартал",
        body="Просим подписать акт сверки.",
        department_id="00-000002",
        department_name="Бухгалтерия",
        original_department_id="00-000044",
        original_department_name="Юридический отдел",
        path=corrections_file,
    )

    store = load_corrections(corrections_file)
    assert len(store["entries"]) == 1
    assert store["entries"][0]["department_id"] == "00-000002"

    matched = find_correction_match(
        recipient="jurist@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Акт сверки за квартал",
        body="Просим подписать акт сверки.",
        path=corrections_file,
    )
    assert matched is not None
    assert matched["department_id"] == "00-000002"


def test_route_engine_uses_human_correction(corrections_file: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROUTING_CORRECTIONS_PATH", str(corrections_file))
    from agent_pochta.config import reset_settings

    reset_settings()
    reset_route_engine()

    save_routing_correction(
        message_id="<corr@example>",
        sender_email="vendor@example.com",
        recipient="jurist@turbo-don.ru",
        subject="Акт сверки",
        body="Просим подписать.",
        department_id="00-000002",
        department_name="Бухгалтерия",
        path=corrections_file,
    )

    engine = RouteEngine.load()
    email = EmailMessage(
        message_id="<route@example>",
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Акт сверки",
        body_text="Просим подписать.",
        received_at=datetime.now(timezone.utc),
        routing_recipient="jurist@turbo-don.ru",
    )
    decision = engine.route(email, combined_text=email.body_text)
    assert decision.services[0].code == "00-000002"
    assert decision.match_source == "human_correction"


def test_row_to_dict_serializes_timestamps_as_utc():
    ts = datetime(2026, 7, 2, 6, 11, 0)
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<ts@example>",
        received_at=ts,
        processed_at=ts,
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
    )
    data = _row_to_dict(row)
    assert data["received_at"] == "2026-07-02T06:11:00Z"
    assert data["processed_at"] == "2026-07-02T06:11:00Z"


def test_row_to_dict_includes_recipients():
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<meta@example>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        raw_payload_json=json.dumps(
            {
                "message_id": "<meta@example>",
                "mailbox": "info@turbo-don.ru",
                "sender_email": "a@b.ru",
                "received_at": datetime.now(timezone.utc).isoformat(),
                "body_text": "Полный текст письма для UI.",
                "to": ["jurist@turbo-don.ru", "buh@turbo-don.ru"],
                "routing_recipient": "jurist@turbo-don.ru",
            },
            ensure_ascii=False,
        ),
    )
    data = _row_to_dict(row)
    assert data["routing_recipient"] == "jurist@turbo-don.ru"
    assert data["to"] == ["jurist@turbo-don.ru", "buh@turbo-don.ru"]
    assert data["body_text"] == "Полный текст письма для UI."


def test_row_to_dict_body_text_from_html_fallback():
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<html@example>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        raw_payload_json=json.dumps(
            {
                "message_id": "<html@example>",
                "body_text": "",
                "body_html": "<p>Текст из HTML.</p>",
            },
            ensure_ascii=False,
        ),
    )
    assert _row_to_dict(row)["body_text"] == "Текст из HTML."


def test_payload_meta_handles_invalid_json():
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<bad@example>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        raw_payload_json="{not-json",
    )
    assert _payload_meta(row) == {"to": [], "routing_recipient": None}


def test_row_to_dict_empty_body_returns_not_stored_placeholder():
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<empty@example>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        raw_payload_json=json.dumps(
            {
                "message_id": "<empty@example>",
                "body_text": "",
            },
            ensure_ascii=False,
        ),
    )
    assert _row_to_dict(row)["body_text"] == BODY_NOT_STORED_PLACEHOLDER


def test_row_to_list_dict_omits_body_text():
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<list@example>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        raw_payload_json=json.dumps(
            {
                "message_id": "<list@example>",
                "body_text": "Тяжёлый текст письма не должен попадать в список.",
            },
            ensure_ascii=False,
        ),
    )
    data = _row_to_list_dict(row)
    assert "body_text" not in data
    assert _row_to_dict(row)["body_text"] == "Тяжёлый текст письма не должен попадать в список."


def test_list_departments_endpoint():
    from agent_pochta.api.app import app

    client = TestClient(app)
    response = client.get("/api/v1/departments")
    assert response.status_code == 200
    departments = response.json()
    assert len(departments) == 134
    assert all("id" in item and "name" in item for item in departments)
    assert departments == sorted(departments, key=lambda item: item["id"])
    by_id = {item["id"]: item["name"] for item in departments}
    assert by_id["00-000002"] == "Бухгалтерия"
    assert by_id["00-000065"] == "Отдел МТО"
    assert "00-999999" not in by_id
