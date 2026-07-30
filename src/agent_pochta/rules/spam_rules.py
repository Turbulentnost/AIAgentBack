"""Правила спам-фильтрации (ТЗ раздел 4, узел 2.1 + Приложение А СТО-34-238)."""

from __future__ import annotations

import re

from agent_pochta.schemas import EmailMessage, SpamResult

# Приложение А — документы, не подлежащие регистрации
APPENDIX_A_MARKERS: tuple[str, ...] = (
    "рекламн",
    "распродаж",
    "акция",
    "скидк",
    "промо",
    "рассылк",
    "приглашаем на семинар",
    "приглашение на семинар",
    "вебинар",
    "поздравля",
    "с днём рождения",
    "с днем рождения",
    "резюме",
    "cv ",
    "curriculum vitae",
    "предложение о приобретении",
    "unsubscribe",
    "отписаться от рассылки",
    # Высокочастотный шум из IMAP bulk (marketing/CFO/курсы)
    "только в выходные",
    "металлообработк",
    "изготовление деталей",
    "курсон",
    "cfo-russia",
    "cfo russia",
    "бесплатный вебинар",
    "зарегистрируйтесь на вебинар",
)

# Управляемые списки (в проде — из БД / API платформы)
BLACKLIST_DOMAINS: set[str] = set()
BLACKLIST_ADDRESSES: set[str] = set()
STOP_WORDS: set[str] = {
    "выгодное предложение",
    "только сегодня",
    "розыгрыш",
    "click here",
    "buy now",
}

SUSPICIOUS_DOMAIN_SUFFIXES: tuple[str, ...] = (
    ".xyz",
    ".top",
    ".click",
    ".work",
    ".buzz",
)

FREE_MAIL_DOMAINS: set[str] = {
    "gmail.com",
    "mail.ru",
    "yandex.ru",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
}

_COMMERCIAL_CONTEXT_MARKERS: tuple[str, ...] = (
    "ткп",
    "коммерческ",
    "счет",
    "счёт",
    "заказ",
    "поставк",
)


def _email_domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower().strip()


def check_rule_spam(email: EmailMessage) -> SpamResult | None:
    """Этап 2.1 — проверка по правилам. None = правило не сработало."""
    sender = email.sender_email.lower().strip()
    domain = _email_domain(sender)
    text = f"{email.subject} {email.body_text}".lower()

    if sender in {a.lower() for a in BLACKLIST_ADDRESSES}:
        return SpamResult(
            is_spam=True,
            confidence=1.0,
            reason="Адрес отправителя в чёрном списке",
            rule_hit="blacklist_address",
        )

    if domain in BLACKLIST_DOMAINS:
        return SpamResult(
            is_spam=True,
            confidence=1.0,
            reason="Домен отправителя в чёрном списке",
            rule_hit="blacklist_domain",
        )

    for word in STOP_WORDS:
        if word in text:
            return SpamResult(
                is_spam=True,
                confidence=0.99,
                reason=f"Стоп-слово: {word}",
                rule_hit="stop_word",
            )

    for marker in APPENDIX_A_MARKERS:
        if marker in text:
            # «Скидка» в переписке по ТКП/заказу — обычные коммерческие
            # переговоры, а не самостоятельный признак рекламной рассылки.
            if marker == "скидк" and any(
                context in text for context in _COMMERCIAL_CONTEXT_MARKERS
            ):
                continue
            return SpamResult(
                is_spam=True,
                confidence=0.95,
                reason=f"Приложение А СТО-34-238: {marker}",
                rule_hit="appendix_a",
            )

    if email.list_unsubscribe:
        return SpamResult(
            is_spam=True,
            confidence=0.92,
            reason="Поле List-Unsubscribe (массовая рассылка)",
            rule_hit="list_unsubscribe",
        )

    if any(domain.endswith(suffix) for suffix in SUSPICIOUS_DOMAIN_SUFFIXES):
        return SpamResult(
            is_spam=True,
            confidence=0.90,
            reason=f"Подозрительный домен отправителя: {domain}",
            rule_hit="suspicious_domain",
        )

    marketing_theme = re.search(r"(акци|скидк|sale|offer|promo)", email.subject, re.I)
    if marketing_theme and not email.reply_to and domain in FREE_MAIL_DOMAINS:
        return SpamResult(
            is_spam=True,
            confidence=0.88,
            reason="Маркетинговая тема без Reply-To с публичного почтового домена",
            rule_hit="no_reply_to_marketing",
        )

    return None
