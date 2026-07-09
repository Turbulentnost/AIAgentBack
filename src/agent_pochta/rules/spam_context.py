"""Контекст для спам-фильтра: доверенные домены, пересылки, промпт LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent_pochta.config import Settings
from agent_pochta.schemas import EmailMessage, SpamResult

_FORWARD_SUBJECT = re.compile(
    r"^(?:(?:fw|fwd|включ\.?\s*сообщ|пересыл|пересл|re|ответ)\s*:\s*)+",
    re.IGNORECASE,
)
_FORWARD_BODY = re.compile(
    r"(?im)"
    r"(?:^[-—=]{2,}\s*(?:пересланное сообщение|forwarded message|original message))"
    r"|(?:^от:\s+\S)"
    r"|(?:^from:\s+\S)"
)
_EMBEDDED_FROM = re.compile(
    r"(?im)^(?:from|от|отправитель)\s*:\s*(?:[^<\n]*<)?([^>\s@]+@[^>\s\n]+)",
)


def email_domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower().strip()


def is_trusted_sender(email: EmailMessage, settings: Settings) -> bool:
    domain = email_domain(email.sender_email)
    return domain in settings.trusted_domain_list


def is_forwarded(email: EmailMessage) -> bool:
    subject = (email.subject or "").strip()
    if _FORWARD_SUBJECT.match(subject):
        return True
    body = email.body_text or ""
    return bool(_FORWARD_BODY.search(body[:8000]))


def detect_embedded_sender(body: str) -> str | None:
    match = _EMBEDDED_FROM.search(body[:8000])
    if not match:
        return None
    return match.group(1).lower().strip()


@dataclass(frozen=True)
class SpamAnalysisContext:
    is_trusted_sender: bool
    is_forwarded: bool
    embedded_sender: str | None


def analyze_spam_context(email: EmailMessage, settings: Settings) -> SpamAnalysisContext:
    body = email.body_text or ""
    return SpamAnalysisContext(
        is_trusted_sender=is_trusted_sender(email, settings),
        is_forwarded=is_forwarded(email),
        embedded_sender=detect_embedded_sender(body),
    )


def trusted_sender_pass(email: EmailMessage, settings: Settings) -> SpamResult | None:
    """После правил 2.1: доверенный домен → не вызываем LLM (нет ложных mismatch)."""
    if not settings.spam_skip_llm_for_trusted:
        return None
    if not is_trusted_sender(email, settings):
        return None
    return SpamResult(
        is_spam=False,
        confidence=0.05,
        reason="Доверенный корпоративный отправитель, правила спама пройдены",
        rule_hit="trusted_sender",
    )


def build_spam_llm_messages(email: EmailMessage, settings: Settings) -> tuple[str, str]:
    ctx = analyze_spam_context(email, settings)
    trusted_domains = ", ".join(f"@{d}" for d in settings.trusted_domain_list) or "@turbo-don.ru"

    system = (
        "Ты классификатор спама для корпоративной почты НПО «Турбулентность-ДОН». "
        "confidence — вероятность того, что письмо СПАМ (0.0 = точно не спам, 1.0 = точно спам). "
        "Ответь строго JSON: "
        '{"is_spam": bool, "confidence": float 0..1, "reason": "строка на русском"}\n\n'
        "Правила:\n"
        f"- Доверенные домены сотрудников: {trusted_domains}. Письма с них — не спам, "
        "если нет явной рекламы или фишинга.\n"
        "- Пересланное письмо (FW:/Fwd:/Пересл:/Re: или блок «Пересланное сообщение»): "
        "несовпадение домена From и компании в тексте — НОРМАЛЬНО, это не признак спама.\n"
        "- Reply-To может указывать на контрагента; оригинальный отправитель может быть в теле.\n"
        "- Деловые запросы (акт сверки, счёт, договор, подпись) от контрагентов — не спам.\n"
        "- Спам: массовая реклама, семинары/вебинары, фишинг банков, unsubscribe, "
        "«только сегодня», подозрительные ссылки без делового контекста."
    )

    lines = [
        f"От: {email.sender_email}",
        f"Имя отправителя: {email.sender_name or '—'}",
    ]
    if email.reply_to:
        lines.append(f"Reply-To: {email.reply_to}")
    lines.append(f"Тема: {email.subject}")
    if ctx.is_forwarded:
        lines.append("Метка: пересланное письмо")
    if ctx.is_trusted_sender:
        lines.append("Метка: отправитель с доверенного корпоративного домена")
    if ctx.embedded_sender:
        lines.append(f"Оригинальный отправитель в тексте: {ctx.embedded_sender}")
    lines.extend(["", (email.body_text or "")[:4000]])

    return system, "\n".join(lines)
