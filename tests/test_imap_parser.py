"""Тесты парсера IMAP/MIME."""

from __future__ import annotations

from email.message import EmailMessage as StdEmailMessage

from agent_pochta.imap.parser import parse_raw_email


def _build_raw(subject: str, body: str, sender: str = "client@example.com") -> bytes:
    msg = StdEmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Message-ID"] = "<parser-test@example.com>"
    msg.set_content(body)
    return msg.as_bytes()


def test_parse_plain_email():
    parsed = parse_raw_email(_build_raw("Счёт", "Просим выставить счёт."), "info@turbo-don.ru")
    assert parsed.subject == "Счёт"
    assert "счёт" in parsed.body_text.lower()
    assert parsed.sender_email == "client@example.com"
    assert parsed.mailbox == "info@turbo-don.ru"


def test_parse_to_and_cc_separately():
    msg = StdEmailMessage()
    msg["From"] = "client@example.com"
    msg["To"] = "tender@turbo-don.ru"
    msg["Cc"] = "jurist@turbo-don.ru, info@turbo-don.ru"
    msg["Subject"] = "Test"
    msg["Message-ID"] = "<to-cc@example.com>"
    msg.set_content("Body")
    parsed = parse_raw_email(msg.as_bytes(), "info@turbo-don.ru")
    assert parsed.to == ["tender@turbo-don.ru"]
    assert parsed.cc == ["jurist@turbo-don.ru", "info@turbo-don.ru"]


def test_parse_list_unsubscribe_header():
    msg = StdEmailMessage()
    msg["From"] = "promo@example.com"
    msg["Subject"] = "News"
    msg["Message-ID"] = "<unsub@example.com>"
    msg["List-Unsubscribe"] = "<mailto:unsub@example.com>"
    msg.set_content("Body")
    parsed = parse_raw_email(msg.as_bytes(), "info@turbo-don.ru")
    assert parsed.list_unsubscribe is not None


def test_parse_load_oversized_attachments_keeps_content(monkeypatch):
    from agent_pochta.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "max_attachment_mb", 0)  # любой файл > лимита

    msg = StdEmailMessage()
    msg["From"] = "client@example.com"
    msg["Subject"] = "Big"
    msg["Message-ID"] = "<big@example.com>"
    msg.set_content("Body")
    msg.add_attachment(b"ABCDEFGH", maintype="application", subtype="pdf", filename="big.pdf")

    limited = parse_raw_email(msg.as_bytes(), "info@turbo-don.ru")
    assert limited.attachments[0].content is None
    assert limited.attachments[0].size_bytes == 8

    full = parse_raw_email(
        msg.as_bytes(),
        "info@turbo-don.ru",
        load_oversized_attachments=True,
    )
    assert full.attachments[0].content == b"ABCDEFGH"
