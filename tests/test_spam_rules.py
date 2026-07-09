"""Правила спам-фильтрации."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_pochta.rules.spam_rules import check_rule_spam
from agent_pochta.schemas import EmailMessage


def _email(**kwargs) -> EmailMessage:
    base = dict(
        message_id="<t@example>",
        mailbox="info@turbo-don.ru",
        sender_email="client@example.com",
        subject="Запрос",
        body_text="Обычное деловое письмо без маркеров.",
        received_at=datetime.now(timezone.utc),
    )
    base.update(kwargs)
    return EmailMessage(**base)


def test_stop_word_triggers_spam():
    result = check_rule_spam(_email(body_text="Только сегодня выгодное предложение!"))
    assert result is not None
    assert result.is_spam
    assert result.rule_hit == "stop_word"


def test_appendix_a_seminar_invite():
    result = check_rule_spam(_email(subject="Приглашение на семинар", body_text="Ждём вас"))
    assert result is not None
    assert result.rule_hit == "appendix_a"


def test_list_unsubscribe_is_spam():
    result = check_rule_spam(_email(list_unsubscribe="mailto:unsub@example.com"))
    assert result is not None
    assert result.rule_hit == "list_unsubscribe"


def test_clean_email_passes_rules():
    assert check_rule_spam(_email()) is None


def test_supply_email_with_components_not_spam():
    """Заказ/поставка комплектующих — деловая переписка, не Приложение А."""
    result = check_rule_spam(
        _email(body_text="Просим счёт на комплектующих для промышленного оборудования по договору.")
    )
    assert result is None
