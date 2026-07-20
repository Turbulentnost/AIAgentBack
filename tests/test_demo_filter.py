"""Тесты фильтра демо/тестовых писем."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_pochta.demo_filter import is_demo_email, is_demo_message
from agent_pochta.schemas import EmailMessage


def _email(**kwargs) -> EmailMessage:
    base = dict(
        message_id="<real@client.ru>",
        mailbox="info@turbo-don.ru",
        sender_email="client@client.ru",
        subject="Реальное письмо",
        body_text="Текст",
        received_at=datetime.now(timezone.utc),
    )
    base.update(kwargs)
    return EmailMessage(**base)


def test_demo_domains_detected():
    assert is_demo_message(message_id="<x@y>", sender_email="zakaz@romashka.ru")
    assert is_demo_message(message_id="<x@y>", sender_email="promo@spam.example")
    assert is_demo_message(message_id="<x@y>", sender_email="no-reply@service.example")


def test_demo_message_ids_detected():
    assert is_demo_message(message_id="<demo-1@romashka.ru>", sender_email="a@b.ru")
    assert is_demo_message(message_id="<enqueue-demo-1@local>", sender_email="a@b.ru")
    assert is_demo_message(message_id="<tender-happy@example>", sender_email="zakaz@romashka.ru")
    assert is_demo_message(
        message_id="<g@example>#jurist@turbo-don.ru",
        sender_email="info@nalog.gov.ru",
    )


def test_real_messages_not_detected():
    assert not is_demo_message(message_id="<abc@mail.ru>", sender_email="client@mail.ru")
    assert not is_demo_message(
        message_id="<notice@nalog.gov.ru>",
        sender_email="info@nalog.gov.ru",
    )
    assert not is_demo_email(_email())
