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


def parse_routing_message_id(message_id: str) -> tuple[str, str | None]:
    """Разбирает base Message-ID и routing recipient, снимая повторные суффиксы."""
    base = message_id
    recipient: str | None = None
    while "#" in base:
        candidate_base, candidate_suffix = base.rsplit("#", 1)
        if "@" not in candidate_suffix:
            break
        recipient = candidate_suffix.lower()
        base = candidate_base
    if recipient is None:
        return message_id, None
    return base, recipient


def normalize_routing_email(email: EmailMessage) -> EmailMessage:
    """Приводит message_id к base и переносит recipient из суффикса id."""
    base_id, routing_from_id = parse_routing_message_id(email.message_id)
    if routing_from_id is None:
        return email
    updates: dict = {"message_id": base_id}
    if not email.routing_recipient:
        updates["routing_recipient"] = routing_from_id
    return email.model_copy(update=updates)
