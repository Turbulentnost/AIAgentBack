"""Тесты дообучения базы на коррекциях оператора."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_pochta.routing.learning import (
    collect_department_learning_keywords,
    enrich_department_in_qdrant,
    enrich_hitl_contractor_in_qdrant,
    learn_from_not_spam,
    learn_from_routing_correction,
    learn_from_spam_mark,
)
from agent_pochta.rules.spam_learning import load_spam_learning, save_spam_pattern
from agent_pochta.services.rag_qdrant import _append_department_keywords_impl


@pytest.fixture
def corrections_file(tmp_path: Path) -> Path:
    path = tmp_path / "routing_corrections.json"
    path.write_text('{"version": "1.0", "entries": []}\n', encoding="utf-8")
    return path


@pytest.fixture
def learning_file(tmp_path: Path) -> Path:
    path = tmp_path / "spam_learning_patterns.json"
    path.write_text('{"version": "2.0", "entries": []}\n', encoding="utf-8")
    return path


def test_collect_department_learning_keywords_includes_recipient_local_part():
    entry = {
        "keywords": ["акт сверки", "сверка"],
        "recipient": "jurist@turbo-don.ru",
    }
    keywords = collect_department_learning_keywords(entry)
    assert "акт сверки" in keywords
    assert "jurist" in keywords


def test_append_department_keywords_merges_unique():
    client = MagicMock()
    client.scroll.return_value = (
        [
            MagicMock(
                id="point-1",
                vector=[0.0, 0.0, 0.0, 0.0],
                payload={
                    "department_id": "00-000002",
                    "department_name": "Бухгалтерия",
                    "keywords": ["акт", "сверка"],
                },
            )
        ],
        None,
    )

    result = _append_department_keywords_impl(
        client,
        "00-000002",
        ["сверка", "квартал", "акт"],
    )

    assert result["updated"] is True
    assert result["keywords_added"] == 1
    assert result["added_keywords"] == ["квартал"]
    client.upsert.assert_called_once()
    upserted = client.upsert.call_args.kwargs["points"][0]
    assert upserted.payload["keywords"] == ["акт", "сверка", "квартал"]


def test_learn_from_routing_correction_stub_skips_qdrant(
    corrections_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ROUTING_CORRECTIONS_PATH", str(corrections_file))
    monkeypatch.setenv("RAG_BACKEND", "stub")
    from agent_pochta.config import reset_settings

    reset_settings()

    result = learn_from_routing_correction(
        message_id="<learn@example>",
        sender_email="vendor@example.com",
        recipient="buh@turbo-don.ru",
        subject="Акт сверки за квартал",
        body="Просим подписать акт сверки.",
        department_id="00-000002",
        department_name="Бухгалтерия",
        path=corrections_file,
    )

    assert result["correction_saved"] is True
    assert result["correction_id"]
    assert result["keywords_added"] == 0
    assert result["qdrant_updated"] is False


def test_learn_from_routing_correction_enriches_qdrant(
    corrections_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ROUTING_CORRECTIONS_PATH", str(corrections_file))
    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    from agent_pochta.config import reset_settings

    reset_settings()

    with patch(
        "agent_pochta.services.rag_qdrant.append_department_keywords",
        return_value={
            "updated": True,
            "keywords_added": 2,
            "added_keywords": ["акт сверки", "buh"],
        },
    ) as append_mock:
        result = learn_from_routing_correction(
            message_id="<learn@example>",
            sender_email="vendor@example.com",
            recipient="buh@turbo-don.ru",
            subject="Акт сверки за квартал",
            body="Просим подписать акт сверки.",
            department_id="00-000002",
            department_name="Бухгалтерия",
            path=corrections_file,
        )

    assert result["correction_saved"] is True
    assert result["qdrant_updated"] is True
    assert result["keywords_added"] == 2
    append_mock.assert_called_once()
    assert append_mock.call_args.args[1] == "00-000002"


def test_enrich_hitl_contractor_stub_skips(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RAG_BACKEND", "stub")
    from agent_pochta.config import reset_settings

    reset_settings()
    result = enrich_hitl_contractor_in_qdrant(
        contractor_id="email:a@b.ru",
        name="Partner",
        email="a@b.ru",
    )
    assert result["upserted"] == 0
    assert result["reason"] == "stub_backend"


def test_enrich_department_in_qdrant_stub_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RAG_BACKEND", "stub")
    from agent_pochta.config import reset_settings

    reset_settings()
    result = enrich_department_in_qdrant("00-000002", ["сверка"])
    assert result["updated"] is False
    assert result["reason"] == "stub_backend"


def test_learn_from_not_spam_saves_antipattern_and_removes_spam(
    learning_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning_file))
    monkeypatch.setenv("RAG_BACKEND", "stub")
    from agent_pochta.config import reset_settings

    reset_settings()
    save_spam_pattern(
        message_id="<learn@example>",
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар",
        body="Рекламная рассылка",
        spam_reason="Реклама",
        path=learning_file,
    )

    result = learn_from_not_spam(
        message_id="<learn@example>",
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар",
        body="Рекламная рассылка",
        reason="Не спам",
        path=learning_file,
    )

    assert result["spam_pattern_removed"] is True
    assert result["removed_count"] == 1
    assert result["antipattern_saved"] is True
    store = load_spam_learning(learning_file)
    assert len(store["entries"]) == 1
    assert store["entries"][0]["label"] == "not_spam"
    assert store["entries"][0]["reason"] == "Не спам"


def test_learn_from_spam_mark_returns_qdrant_flag(
    learning_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning_file))
    monkeypatch.setenv("RAG_BACKEND", "stub")
    from agent_pochta.config import reset_settings

    reset_settings()

    result = learn_from_spam_mark(
        message_id="<spam@example>",
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар",
        body="Рекламная рассылка",
        spam_reason="Реклама",
        path=learning_file,
    )

    assert result["spam_pattern_saved"] is True
    assert result["spam_pattern_id"]
    assert result["qdrant_synced"] is False


def test_learn_from_spam_mark_syncs_qdrant(
    learning_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning_file))
    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    from agent_pochta.config import reset_settings

    reset_settings()

    with patch(
        "agent_pochta.services.spam_learning_rag_qdrant.upsert_spam_learning_entry"
    ) as upsert_mock:
        result = learn_from_spam_mark(
            message_id="<spam@example>",
            sender_email="promo@spam-offers.xyz",
            subject="Вебинар",
            body="Рекламная рассылка",
            spam_reason="Реклама",
            path=learning_file,
        )

    assert result["qdrant_synced"] is True
    upsert_mock.assert_called_once()
