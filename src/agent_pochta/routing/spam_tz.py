"""Расширенные правила спама из ТЗ §9 (детерминированные условия)."""

from __future__ import annotations

import re

from agent_pochta.schemas import EmailMessage, SpamResult

_NOREPLY_PATTERNS = (
    "noreply",
    "no-reply",
    "do-not-reply",
    "noreplybrif",
    "mailer-daemon",
    "postmaster",
)

_TRANSPORT_MARKERS = (
    "деловые линии",
    "пэк",
    "сдэк",
    "dellin",
    "pecom",
    "cdek",
)

_EDUCATION_MARKERS = (
    "вебинар",
    "семинар",
    "конференц",
    "тренинг",
    "мастер-класс",
    "курс",
)

_EMPTY_BODY_MARKERS = (
    "спасибо",
    "принято",
    "ответ будет позже",
    "подтверждение получения",
)

_GOV_EXCEPTION = (
    "налог",
    "фнс",
    "суд",
    "арбитраж",
    "прокуратур",
    "госорган",
    "требование",
    "исполнительный лист",
)


def check_tz_spam(email: EmailMessage, *, recipient: str | None = None) -> SpamResult | None:
    """Дополнительные правила спама ТЗ §9. None — правило не сработало."""
    sender = email.sender_email.lower()
    local = sender.split("@", 1)[0]
    text = f"{email.subject} {email.body_text}".lower()
    recipient = (recipient or email.routing_recipient or email.mailbox or "").lower()

    if any(p in sender for p in _NOREPLY_PATTERNS) or any(p in local for p in _NOREPLY_PATTERNS):
        if not any(ex in text for ex in _GOV_EXCEPTION):
            if len(text.strip()) < 80 or not email.attachments:
                return SpamResult(
                    is_spam=True,
                    confidence=0.96,
                    reason="Технический noreply-адрес без предмета действия (ТЗ §9)",
                    rule_hit="tz_noreply",
                )

    if any(m in text for m in _EDUCATION_MARKERS):
        industry = ("газ", "нефт", "промышлен", "расходомер", "ufg", "tfg")
        if not any(i in text for i in industry):
            return SpamResult(
                is_spam=True,
                confidence=0.93,
                reason="Образовательное мероприятие вне профиля (ТЗ §9)",
                rule_hit="tz_education",
            )

    if any(m in text for m in _TRANSPORT_MARKERS):
        action = ("заявк", "доставк", "получ", "забер", "отправ")
        if not any(a in text for a in action):
            return SpamResult(
                is_spam=True,
                confidence=0.91,
                reason="Транспортное уведомление без предмета действия (ТЗ §9)",
                rule_hit="tz_transport",
            )

    # ОМТО без вложений
    if "omto" in recipient and not email.attachments:
        omto_work = ("счет", "счёт", "упд", "эдо", "отгрузк", "образц")
        if not any(w in text for w in omto_work):
            return SpamResult(
                is_spam=True,
                confidence=0.90,
                reason="Письмо в ОМТО без вложений документов (ТЗ §9)",
                rule_hit="tz_omto_no_attach",
            )

    stripped = re.sub(r"\s+", " ", text).strip()
    if len(stripped) < 25 and any(m in stripped for m in _EMPTY_BODY_MARKERS):
        return SpamResult(
            is_spam=True,
            confidence=0.89,
            reason="Уведомление без предмета действия (ТЗ §9)",
            rule_hit="tz_empty_notice",
        )

    if any(ex in text for ex in _GOV_EXCEPTION):
        return None

    return None
