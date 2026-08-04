"""Tests for email vector indexing (BGE → Qdrant)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

from agent_pochta.services.email_indexing import (
    build_indexing_text_from_row,
    chunk_text,
    index_email_row,
    reextract_full_embedding_text,
)
from agent_pochta.services.embedding_client import embed_texts


def test_chunk_text_splits_long_body():
    text = "абв " * 2000
    chunks = chunk_text(text, max_chars=100, overlap=10)
    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)


def test_build_indexing_text_from_row_uses_embedding_source():
    row = MagicMock()
    row.subject = "Счёт"
    row.sender_email = "a@b.ru"
    row.mailbox = "info@turbo-don.ru"
    row.summary_ru = "Кратко"
    row.attachments = []
    row.raw_payload_json = json.dumps(
        {"embedding_source_text": "Тема: Счёт\n\nТекст вложения PDF"}
    )
    text = build_indexing_text_from_row(row)
    assert "Текст вложения PDF" in text


def test_index_email_row_skips_when_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EMAIL_RAG_ENABLED", "false")
    from agent_pochta.config import reset_settings

    reset_settings()
    repo = MagicMock()
    row = MagicMock()
    result = index_email_row(repo, row)
    assert result["skipped"] is True
    assert result["reason"] == "disabled"


@patch("agent_pochta.services.email_indexing.upsert_email_chunks")
@patch("agent_pochta.services.email_indexing.embed_texts")
def test_index_email_row_upserts_chunks(mock_embed, mock_upsert, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EMAIL_RAG_ENABLED", "true")
    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("EMBEDDING_VECTOR_SIZE", "1024")
    from agent_pochta.config import reset_settings

    reset_settings()

    row = MagicMock()
    row.id = uuid.uuid4()
    row.message_id = "msg-1"
    row.mailbox = "info@turbo-don.ru"
    row.subject = "Тест"
    row.status = "done"
    row.department_id = "00-000001"
    row.received_at = None
    row.attachments = []
    row.raw_payload_json = json.dumps(
        {"embedding_source_text": "Длинный текст письма с содержимым вложения PDF и описанием счёта на оплату"}
    )

    def _fake_embed(texts, **kwargs):
        return [[0.1] * 1024 for _ in texts]

    mock_embed.side_effect = _fake_embed
    mock_upsert.return_value = 1

    repo = MagicMock()
    result = index_email_row(repo, row, force=True)
    assert result["ok"] is True
    assert result["chunks"] == 1
    mock_embed.assert_called_once()
    mock_upsert.assert_called_once()


@patch("agent_pochta.attachments.pipeline.process_email_attachments")
@patch("agent_pochta.attachments.imap_fetch.ensure_attachments_from_imap")
@patch("agent_pochta.imap.body_fetch.fetch_and_cache_email_body")
@patch("agent_pochta.imap.body_fetch.row_has_cached_body", return_value=False)
@patch("agent_pochta.workers.runtime.get_worker_container")
def test_reextract_full_embedding_text(
    mock_container,
    _mock_has_body,
    mock_fetch_body,
    mock_ensure_att,
    mock_process,
):
    mock_container.return_value = MagicMock(vault=MagicMock(), documents=MagicMock())
    mock_fetch_body.return_value = MagicMock(ok=True, cached=False, reason=None)

    row = MagicMock()
    row.id = uuid.uuid4()
    row.raw_payload_json = json.dumps({"message_id": "<x@y>", "attachments": []})
    row.attachments = []

    email = MagicMock()
    email.attachments = []
    repo = MagicMock()
    repo.load_email_from_row.return_value = email
    mock_process.return_value = MagicMock(combined_text="Тема\n\nПолное тело письма из IMAP")

    result = reextract_full_embedding_text(repo, row)
    assert result["ok"] is True
    assert result["text_len"] > 20
    payload = json.loads(row.raw_payload_json)
    assert "Полное тело" in payload["embedding_source_text"]


@patch("httpx.Client.post")
def test_embed_texts_openai_format(mock_post, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://192.168.1.157:8080/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    monkeypatch.setenv("EMBEDDING_VECTOR_SIZE", "3")
    from agent_pochta.config import reset_settings

    reset_settings()

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "data": [
            {"index": 0, "embedding": [0.1, 0.2, 0.3]},
        ]
    }
    mock_post.return_value = response

    vectors = embed_texts(["hello"])
    assert vectors == [[0.1, 0.2, 0.3]]
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json"]["model"] == "BAAI/bge-m3"
