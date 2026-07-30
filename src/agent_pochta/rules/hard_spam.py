"""Жёсткий (детерминированный) спам — этап 2.1 и ТЗ §9.

Не включает:
- LLM-классификатор (мягкая оценка, rule_hit отсутствует);
- обучение оператора (learned_spam_pattern);
- исключения ministry_not_spam / trusted_sender.
"""

from __future__ import annotations

from agent_pochta.rules.spam_rules import check_rule_spam
from agent_pochta.routing.spam_tz import check_tz_spam
from agent_pochta.schemas import EmailMessage, SpamResult

# rule_hit из spam_rules.py (этап 2.1) и spam_tz.py (ТЗ §9).
HARD_SPAM_RULE_HITS: frozenset[str] = frozenset(
    {
        "blacklist_address",
        "blacklist_domain",
        "stop_word",
        "appendix_a",
        "list_unsubscribe",
        "suspicious_domain",
        "no_reply_to_marketing",
        "tz_noreply",
        "tz_education",
        "tz_transport",
        "tz_omto_no_attach",
        "tz_empty_notice",
    }
)


def is_hard_spam(spam: SpamResult | None) -> bool:
    """True, если спам зафиксирован жёстким правилом (не LLM и не learned_spam_pattern)."""
    if spam is None or not spam.is_spam:
        return False
    rule_hit = (spam.rule_hit or "").strip()
    return rule_hit in HARD_SPAM_RULE_HITS


def detect_hard_spam(
    email: EmailMessage,
    *,
    recipient: str | None = None,
) -> SpamResult | None:
    """Проверка жёсткими правилами без LLM и без обучения оператора."""
    rule_result = check_rule_spam(email)
    if rule_result is not None:
        return rule_result
    return check_tz_spam(
        email,
        recipient=recipient or email.routing_recipient or email.mailbox,
    )
