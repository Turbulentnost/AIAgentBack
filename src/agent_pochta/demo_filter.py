"""Фильтрация демо/тестовых писем (run_demo.py, enqueue_demo.py, pytest fixtures).

Такие записи не должны попадать в production UI и PostgreSQL.
"""

from __future__ import annotations

from sqlalchemy import or_

from agent_pochta.schemas import EmailMessage

# Домены из демо-скриптов, enqueue_demo (@local) и unit-тестов (example.ru, …).
DEMO_SENDER_DOMAINS: frozenset[str] = frozenset(
    {
        "romashka.ru",
        "spam.example",
        "service.example",
        "example.ru",
        "example.com",
        "local",
    }
)

# Маркеры Message-ID из run_demo / enqueue_demo / pytest.
DEMO_MESSAGE_ID_MARKERS: tuple[str, ...] = (
    "demo-",
    "enqueue-demo",
    "@example>",
    "@example.ru>",
    "@example.com>",
    "@test>",
    "@local>",
    "tender-happy@",
    "<t@example",
    "<s@example",
    "<g@example",
    "<noreply@example",
)


def _sender_domain(sender_email: str) -> str:
    addr = sender_email.strip().lower()
    if "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[-1]


def is_demo_message(*, message_id: str, sender_email: str) -> bool:
    """True для писем из демо-скриптов и тестовых фикстур."""
    mid = (message_id or "").lower()
    domain = _sender_domain(sender_email or "")

    if domain in DEMO_SENDER_DOMAINS:
        return True

    if any(marker in mid for marker in DEMO_MESSAGE_ID_MARKERS):
        return True

    # run_demo.py: <demo-2@nalog.gov.ru>
    if "demo-" in mid and "nalog.gov.ru" in mid:
        return True

    return False


def is_demo_email(email: EmailMessage) -> bool:
    return is_demo_message(message_id=email.message_id, sender_email=email.sender_email)


def is_demo_payload(payload: dict) -> bool:
    return is_demo_message(
        message_id=str(payload.get("message_id") or ""),
        sender_email=str(payload.get("sender_email") or ""),
    )


def demo_row_filter(model):
    """SQLAlchemy-условие: строка email_messages — демо/тест."""
    conditions = [
        model.sender_email.ilike(f"%@{domain}") for domain in sorted(DEMO_SENDER_DOMAINS)
    ]
    for marker in DEMO_MESSAGE_ID_MARKERS:
        conditions.append(model.message_id.ilike(f"%{marker}%"))
    conditions.append(
        model.message_id.ilike("%demo-%") & model.sender_email.ilike("%nalog.gov.ru%")
    )
    return or_(*conditions)
