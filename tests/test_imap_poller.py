"""Тесты догоняющего IMAP-опроса."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from agent_pochta.config import Settings
from agent_pochta.imap.poller import catchup_since_date, merge_emails_by_message_id
from agent_pochta.schemas import EmailMessage


def _email(message_id: str) -> EmailMessage:
    received_at = datetime.now(timezone.utc)
    return EmailMessage(
        message_id=message_id,
        mailbox="test_ii@turbo-don.ru",
        sender_email="sender@example.com",
        subject="Test",
        body_text="Body",
        received_at=received_at,
    )


def test_merge_emails_by_message_id_deduplicates():
    first = _email("<a@example>")
    second = _email("<b@example>")
    duplicate = _email("<a@example>")

    merged = merge_emails_by_message_id([first, second], [duplicate])

    assert [email.message_id for email in merged] == ["<a@example>", "<b@example>"]


def test_catchup_since_date_uses_last_received_minus_one_day():
    last_received = datetime(2026, 7, 9, 15, 0, 0)

    since = catchup_since_date(last_received, settings=Settings())

    assert since == date(2026, 7, 8)


def test_catchup_since_date_falls_back_to_config_window():
    settings = Settings(IMAP_CATCHUP_DAYS=5)

    since = catchup_since_date(None, settings=settings)

    assert since == date.today() - timedelta(days=5)
