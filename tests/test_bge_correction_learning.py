"""Tests for realtime BGE correction learning."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

from agent_pochta.services.bge_correction_learning import upsert_correction_from_row


def test_upsert_skips_unchanged_department() -> None:
    repo = MagicMock()
    row = MagicMock()
    row.id = uuid.uuid4()
    result = upsert_correction_from_row(
        repo,
        row,
        wrong_dept_id="00-000065",
        wrong_dept_name="МТО",
        correct_dept_id="00-000065",
        correct_dept_name="МТО",
    )
    assert result["skipped"] is True
    assert result["reason"] == "dept_unchanged"


def test_upsert_calls_sync_and_updates_payload() -> None:
    repo = MagicMock()
    row = MagicMock()
    row.id = uuid.uuid4()
    row.raw_payload_json = json.dumps({})
    row.sender_email = "a@b.ru"
    row.subject = "Тема"
    row.message_id = "<mid>"
    row.mailbox = "sales@turbo-don.ru"
    row.attachments = []

    settings = MagicMock()
    settings.email_rag_min_chars = 10
    settings.qdrant_url = "http://qdrant:6333"

    with (
        patch(
            "agent_pochta.services.bge_correction_learning.resolve_embed_text_for_row",
            return_value=("Длинный текст письма для embedding", {"reextract": False}),
        ),
        patch(
            "agent_pochta.services.bge_correction_learning.sync_department_correction_records",
            return_value={"ok": True, "upserted": 1},
        ) as sync_mock,
        patch(
            "agent_pochta.services.bge_correction_learning.build_correction_record",
            return_value=MagicMock(email_id=str(row.id)),
        ),
    ):
        result = upsert_correction_from_row(
            repo,
            row,
            wrong_dept_id="00-000066",
            wrong_dept_name="Резерв",
            correct_dept_id="00-000065",
            correct_dept_name="МТО",
            settings=settings,
        )

    assert result["ok"] is True
    sync_mock.assert_called_once()
    payload = json.loads(row.raw_payload_json)
    assert "bge_correction_indexed_at" in payload
