"""Тесты конвертации RFC822 → Outlook .msg."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from agent_pochta.services.email_msg import (
    eml_bytes_to_msg_bytes,
    is_msg_bytes,
    normalize_attachment_filename,
)


def _sample_eml() -> bytes:
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return (
        f"From: sender@example.com\r\n"
        f"To: info@turbo-don.ru\r\n"
        f"Subject: Тест MSG\r\n"
        f"Date: {now}\r\n"
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        f"Тело письма для конвертации в MSG.\r\n"
    ).encode("utf-8")


def test_eml_bytes_to_msg_bytes_produces_ole_compound_file():
    msg = eml_bytes_to_msg_bytes(_sample_eml())
    assert is_msg_bytes(msg)
    assert len(msg) > 512


def test_normalize_attachment_filename_nfc():
    nfd = "2. Реквизиты СК НСК Раи\u0306йффаи\u0306йзен.pdf"
    nfc = normalize_attachment_filename(nfd)
    assert nfc is not None
    assert "\u0306" not in nfc
    assert nfc.endswith(".pdf")


def test_eml_bytes_to_msg_bytes_roundtrip_subject():
    from aspose.email_foss import msg as msgmod

    msg_bytes = eml_bytes_to_msg_bytes(_sample_eml())
    with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as tmp:
        path = tmp.name
    try:
        Path(path).write_bytes(msg_bytes)
        with msgmod.MapiMessage.from_file(path) as message:
            assert "Тест MSG" in (message.subject or "")
    finally:
        os.unlink(path)
