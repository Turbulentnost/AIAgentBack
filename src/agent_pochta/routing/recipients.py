"""Разделение по получателям поля To (Кому), без Cc (ТЗ FR-03, §8.2 шаг 2)."""

from __future__ import annotations

from agent_pochta.schemas import EmailMessage


def split_routing_recipients(email: EmailMessage) -> list[str]:
    """Возвращает список получателей для отдельных RoutingAttempt."""
    recipients: list[str] = []
    seen: set[str] = set()

    for addr in email.to:
        normalized = addr.lower().strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            recipients.append(normalized)

    if email.routing_recipient:
        normalized = email.routing_recipient.lower().strip()
        if normalized not in seen:
            recipients.append(normalized)

    if not recipients:
        if "@" in email.mailbox:
            recipients.append(email.mailbox.lower().strip())
        else:
            recipients.append("info@turbo-don.ru")

    return recipients


def build_routing_search_text(
    *,
    recipient: str = "",
    subject: str = "",
    body: str = "",
    combined_text: str = "",
) -> str:
    """Текст для keyword/RAG-поиска отдела: получатель (ТЗ прилож. D) + содержимое."""
    recipient = recipient.lower().strip()
    content = combined_text.strip() if combined_text.strip() else "\n\n".join(
        p for p in (subject, body) if p
    )
    if not recipient:
        return content
    local = recipient.split("@", 1)[0] if "@" in recipient else recipient
    prefix = f"{recipient} {local}"
    if not content:
        return prefix
    return f"{prefix}\n\n{content}"


def routing_message_id(base_message_id: str, recipient: str) -> str:
    """Составной id для идемпотентности при нескольких получателях."""
    if not recipient:
        return base_message_id
    return f"{base_message_id}#{recipient.lower()}"
