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
    extract_correction_keywords,
    find_correction_match,
    load_corrections,
    migrate_routing_corrections_store,
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
    assert "message_id" not in store["entries"][0]
    assert store["entries"][0]["subject"] == "акт сверки за квартал"

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
    assert _payload_meta(row) == {"to": [], "routing_recipient": None, "hitl_reason": None}


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


def test_extract_correction_keywords_hybrid_rules():
    keywords = extract_correction_keywords(
        "Re: оплата по спецификациям 3 и 4",
        "В письме содержится информация. Александра Дмитриева.",
        recipient="uk_omto4@turbo-don.ru",
        department_id="00-000065",
    )
    assert keywords[0] == "оплата по спецификациям 3 и 4"
    assert "uk_omto4" in keywords
    assert "александра" not in keywords
    assert "(a.dmitriev@efo.ru)" not in keywords
    assert "содержится" not in keywords
    assert keywords == extract_correction_keywords(
        "Re: оплата по спецификациям 3 и 4",
        "В письме содержится информация. Александра Дмитриева.",
        recipient="uk_omto4@turbo-don.ru",
        department_id="00-000065",
    )


def test_extract_correction_keywords_prefers_distinctive_with_corpus():
    corpus = [
        {
            "id": "1",
            "department_id": "00-000065",
            "subject": "заказ 4745",
            "body": "заказ готов",
        },
        {
            "id": "2",
            "department_id": "00-000002",
            "subject": "скан бг",
            "body": "отправляю скан",
        },
    ]
    mto_keywords = extract_correction_keywords(
        "Re: заказ 4745",
        "Вероника, заказ готов к выдаче",
        recipient="uk_omto10@turbo-don.ru",
        department_id="00-000065",
        corpus_entries=corpus,
    )
    buh_keywords = extract_correction_keywords(
        "скан бг",
        "Коллеги, отправляю оригинал",
        recipient="td_buh3@turbo-don.ru",
        department_id="00-000002",
        corpus_entries=corpus,
    )
    assert "заказ 4745" in mto_keywords or "4745" in mto_keywords
    assert "скан бг" in buh_keywords
    assert "вероника" not in mto_keywords


def test_migrate_routing_corrections_store_strips_message_id_and_recomputes(
    corrections_file: Path,
):
    legacy = {
        "version": "1.0",
        "entries": [
            {
                "id": "legacy-1",
                "created_at": "2026-07-13T07:12:38.128603+00:00",
                "message_id": "<066501dd1296$b31d7730$19586590$@efo.ru>#uk_omto4@turbo-don.ru",
                "sender_email": "a.dmitriev@efo.ru",
                "recipient": "uk_omto4@turbo-don.ru",
                "keywords": [
                    "оплата по спецификациям 3 и 4 и др.",
                    "оплата",
                    "александра",
                    "(a.dmitriev@efo.ru)",
                ],
                "department_id": "00-000065",
                "department_name": "Отдел МТО",
            }
        ],
    }
    corrections_file.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    result = migrate_routing_corrections_store(corrections_file)
    assert result["entries"] == 1
    assert result["message_ids_removed"] == 1

    store = load_corrections(corrections_file)
    entry = store["entries"][0]
    assert "message_id" not in entry
    assert entry["subject"] == "оплата по спецификациям 3 и 4 и др"
    assert "uk_omto4" in entry["keywords"]
    assert "александра" not in entry["keywords"]
