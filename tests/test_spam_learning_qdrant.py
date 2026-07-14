"""Тесты синхронизации обучения спама с Qdrant."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent_pochta.rules.spam_learning import (
    remove_entries_by_message_id,
    resync_spam_learning_to_qdrant,
    save_learning_entry,
    save_spam_pattern,
)


@pytest.fixture
def learning_file(tmp_path: Path) -> Path:
    path = tmp_path / "spam_learning_patterns.json"
    path.write_text('{"version": "2.0", "entries": []}\n', encoding="utf-8")
    return path


def test_save_learning_entry_upserts_qdrant(
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
        entry = save_learning_entry(
            label="spam",
            message_id="<qdrant@example>",
            sender_email="promo@spam-offers.xyz",
            subject="Вебинар",
            body="Рекламная рассылка",
            reason="Реклама",
            path=learning_file,
        )

    assert entry["qdrant_synced"] is True
    upsert_mock.assert_called_once()
    upserted = upsert_mock.call_args.args[1]
    assert upserted["message_id"] == "<qdrant@example>"
    assert upserted["label"] == "spam"


def test_save_spam_pattern_qdrant_synced_flag(
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
        entry = save_spam_pattern(
            message_id="<spam@example>",
            sender_email="promo@spam-offers.xyz",
            subject="Вебинар",
            body="Рекламная рассылка",
            spam_reason="Реклама",
            path=learning_file,
        )

    assert entry["qdrant_synced"] is True
    upsert_mock.assert_called_once()


def test_remove_entries_by_message_id_deletes_from_qdrant(
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
    ):
        save_spam_pattern(
            message_id="<spam@example>",
            sender_email="promo@spam-offers.xyz",
            subject="Вебинар",
            body="Рекламная рассылка",
            spam_reason="Реклама",
            path=learning_file,
        )

    with patch(
        "agent_pochta.services.spam_learning_rag_qdrant.delete_spam_learning_by_message_id",
        return_value=1,
    ) as delete_mock:
        result = remove_entries_by_message_id("<spam@example>", path=learning_file)

    assert result["removed_count"] == 1
    assert result["qdrant_removed"] == 1
    delete_mock.assert_called_once_with("http://qdrant:6333", "<spam@example>", label=None)


def test_resync_spam_learning_to_qdrant(
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
    ):
        save_spam_pattern(
            message_id="<one@example>",
            sender_email="a@example.com",
            subject="Тема 1",
            body="Текст 1",
            spam_reason="Спам 1",
            path=learning_file,
        )
        save_spam_pattern(
            message_id="<two@example>",
            sender_email="b@example.com",
            subject="Тема 2",
            body="Текст 2",
            spam_reason="Спам 2",
            path=learning_file,
        )

    with patch(
        "agent_pochta.services.spam_learning_rag_qdrant.upsert_spam_learning_entry"
    ) as upsert_mock:
        result = resync_spam_learning_to_qdrant(learning_file)

    assert result == {"synced": 2, "total": 2, "pruned": 0}
    assert upsert_mock.call_count == 2


def test_resync_skipped_for_stub_backend(
    learning_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning_file))
    monkeypatch.setenv("RAG_BACKEND", "stub")
    from agent_pochta.config import reset_settings

    reset_settings()
    result = resync_spam_learning_to_qdrant(learning_file)
    assert result["synced"] == 0
    assert result["reason"] == "stub_backend"
