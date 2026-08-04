"""Tests for department knowledge and unified embed text."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from agent_pochta.db.message_filters import resolved_turbo_recipient
from agent_pochta.services.department_knowledge import (
    build_unified_embed_text,
    build_unified_embed_text_from_row,
    _fingerprint,
)


def test_resolved_turbo_recipient_prefers_routing_recipient():
    payload = {
        "routing_recipient": "sales@turbo-don.ru",
        "to": ["test_ii@turbo-don.ru"],
    }
    assert resolved_turbo_recipient(mailbox="info@turbo-don.ru", payload=payload) == "sales@turbo-don.ru"


def test_resolved_turbo_recipient_skips_test_ii():
    payload = {"to": ["test_ii@turbo-don.ru", "tender@turbo-don.ru"]}
    assert resolved_turbo_recipient(mailbox="info@turbo-don.ru", payload=payload) == "tender@turbo-don.ru"


def test_resolved_turbo_recipient_ignores_mailbox_only():
    assert resolved_turbo_recipient(mailbox="info@turbo-don.ru", payload={}) == ""


def test_build_unified_embed_text_includes_attachments():
    text = build_unified_embed_text(
        subject="Счёт",
        sender_email="a@b.ru",
        summary_ru="Оплата",
        body_text="Тело письма",
        attachment_blocks=[("invoice.pdf", "application/pdf", "Сумма 1000")],
    )
    assert "Тема: Счёт" in text
    assert "invoice.pdf" in text
    assert "Сумма 1000" in text


def test_build_unified_embed_text_uses_stored_source():
    stored = "Готовый текст для эмбеддинга"
    text = build_unified_embed_text(
        subject="ignored",
        sender_email="x@y.ru",
        stored_source=stored,
    )
    assert text == stored


def test_build_unified_embed_text_from_row():
    row = MagicMock()
    row.subject = "Договор"
    row.sender_email = "c@d.ru"
    row.summary_ru = "Кратко"
    row.attachments = []
    row.raw_payload_json = json.dumps({"body_text": "Полный текст"})
    text = build_unified_embed_text_from_row(row)
    assert "Договор" in text
    assert "Полный текст" in text


def test_fingerprint_normalizes_subject():
    fp1 = _fingerprint(sender="A@B.RU", recipient="x@turbo-don.ru", subject="  Hello   World  ")
    fp2 = _fingerprint(sender="a@b.ru", recipient="x@turbo-don.ru", subject="hello world")
    assert fp1 == fp2
