"""Tests for operator-verified BGE learning and selection."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

from agent_pochta.services.bge_correction_learning import (
    is_already_bge_verified_indexed,
    is_operator_verified_candidate,
    upsert_verified_from_row,
)


def _row(
    *,
    department_id: str = "00-000065",
    is_spam: bool = False,
    payload: dict | None = None,
) -> MagicMock:
    row = MagicMock()
    row.id = uuid.uuid4()
    row.department_id = department_id
    row.department_name = "МТО"
    row.is_spam = is_spam
    row.raw_payload_json = json.dumps(payload or {})
    row.sender_email = "a@b.ru"
    row.subject = "Тема"
    row.message_id = "<mid>"
    row.mailbox = "sales@turbo-don.ru"
    row.attachments = []
    return row


def test_is_operator_verified_candidate_requires_department_and_not_spam() -> None:
    assert is_operator_verified_candidate(_row(payload={"operator_verified": True})) is True
    assert is_operator_verified_candidate(_row(department_id="", payload={"operator_verified": True})) is False
    assert is_operator_verified_candidate(_row(is_spam=True, payload={"operator_verified": True})) is False


def test_is_operator_verified_candidate_rejects_corrected() -> None:
    row = _row(payload={"operator_verified": True, "operator_corrected": True})
    assert is_operator_verified_candidate(row) is False
    row2 = _row(payload={"operator_verified": True})
    assert is_operator_verified_candidate(row2, has_operator_change=True) is False


def test_is_operator_verified_candidate_accepts_operator_approve_event() -> None:
    row = _row(payload={})
    assert is_operator_verified_candidate(row, has_operator_approve=True) is True


def test_is_already_bge_verified_indexed() -> None:
    row = _row(payload={"bge_verified_indexed_at": "2026-01-01T00:00:00"})
    assert is_already_bge_verified_indexed(row) is True
    assert is_already_bge_verified_indexed(_row()) is False


def test_upsert_verified_skips_without_department() -> None:
    repo = MagicMock()
    row = _row(department_id="")
    result = upsert_verified_from_row(repo, row)
    assert result["skipped"] is True
    assert result["reason"] == "no_department"


def test_upsert_verified_calls_sync_and_updates_payload() -> None:
    repo = MagicMock()
    row = _row(payload={})
    settings = MagicMock()
    settings.email_rag_min_chars = 10

    with (
        patch(
            "agent_pochta.services.bge_correction_learning.resolve_embed_text_for_row",
            return_value=("Длинный текст письма для embedding", {"reextract": False}),
        ),
        patch(
            "agent_pochta.services.bge_correction_learning.sync_department_correction_records",
            return_value={"ok": True, "indexed": 1},
        ) as sync_mock,
        patch(
            "agent_pochta.services.bge_correction_learning.build_correction_record",
            return_value=MagicMock(email_id=str(row.id), source="operator_verified"),
        ) as record_mock,
    ):
        result = upsert_verified_from_row(repo, row, settings=settings)

    assert result["ok"] is True
    sync_mock.assert_called_once()
    record_mock.assert_called_once()
    call_kwargs = record_mock.call_args.kwargs
    assert call_kwargs["wrong_dept_id"] == ""
    assert call_kwargs["correct_dept_id"] == "00-000065"
    assert call_kwargs["source"] == "operator_verified"
    payload = json.loads(row.raw_payload_json)
    assert "bge_verified_indexed_at" in payload
