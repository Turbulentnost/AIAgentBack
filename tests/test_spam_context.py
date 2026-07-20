"""Тесты контекста спам-фильтра (пересылки, доверенные домены)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.config import Settings, reset_settings
from agent_pochta.rules.spam_context import (
    analyze_spam_context,
    build_spam_llm_messages,
    is_forwarded,
    is_trusted_sender,
    trusted_sender_pass,
)
from agent_pochta.schemas import EmailMessage


def _email(**kwargs) -> EmailMessage:
    base = dict(
        message_id="<s@example>",
        mailbox="test_ii@turbo-don.ru",
        sender_email="npo_ii4@turbo-don.ru",
        subject="Запрос",
        body_text="Обычное деловое письмо.",
        received_at=datetime.now(timezone.utc),
    )
    base.update(kwargs)
    return EmailMessage(**base)


@pytest.fixture
def turbo_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("TRUSTED_SENDER_DOMAINS", "turbo-don.ru")
    monkeypatch.setenv("MAILBOXES", "test_ii@turbo-don.ru")
    monkeypatch.setenv("SPAM_SKIP_LLM_FOR_TRUSTED", "true")
    reset_settings()
    from agent_pochta.config import get_settings

    return get_settings()


def test_trusted_sender_from_mailbox_domain(turbo_settings):
    assert is_trusted_sender(_email(sender_email="colleague@turbo-don.ru"), turbo_settings)


def test_external_sender_not_trusted(turbo_settings):
    assert not is_trusted_sender(_email(sender_email="buh@vektor-stroy.ru"), turbo_settings)


def test_forward_detected_by_subject():
    assert is_forwarded(_email(subject="FW: Акт сверки"))
    assert is_forwarded(_email(subject="Пересл: Счёт"))


def test_forward_detected_by_body():
    body = "----- Пересланное сообщение -----\nОт: buh@client.ru\nТема: Счёт"
    assert is_forwarded(_email(subject="Без префикса", body_text=body))


def test_trusted_sender_pass_skips_llm(turbo_settings):
    result = trusted_sender_pass(_email(), turbo_settings)
    assert result is not None
    assert not result.is_spam
    assert result.rule_hit == "trusted_sender"


def test_trusted_pass_disabled_when_flag_off(turbo_settings, monkeypatch):
    monkeypatch.setenv("SPAM_SKIP_LLM_FOR_TRUSTED", "false")
    reset_settings()
    from agent_pochta.config import get_settings

    settings = get_settings()
    assert trusted_sender_pass(_email(), settings) is None


def test_llm_prompt_mentions_forward_and_reply_to(turbo_settings):
    email = _email(
        subject="FW: Акт",
        reply_to="buh@vektor-stroy.ru",
        body_text="----- Пересланное сообщение -----\nОт: buh@vektor-stroy.ru",
    )
    ctx = analyze_spam_context(email, turbo_settings)
    assert ctx.is_forwarded
    assert ctx.embedded_sender == "buh@vektor-stroy.ru"

    _system, user = build_spam_llm_messages(email, turbo_settings)
    assert "Reply-To: buh@vektor-stroy.ru" in user
    assert "пересланное письмо" in user.lower()
