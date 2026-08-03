"""Министерства не могут быть спамом — всегда обрабатываются Операционным директором."""

from __future__ import annotations

from functools import lru_cache

from agent_pochta.routing.normalize import keyword_in_text, normalize_text
from agent_pochta.schemas import EmailMessage, SpamResult
from agent_pochta.services.routing_departments import load_routing_rules


@lru_cache(maxsize=1)
def load_ministry_content_patterns() -> tuple[str, ...]:
    """Общие паттерны министерств из routing_rules.json (ключ ministry_content_patterns)."""
    rules = load_routing_rules()
    patterns = rules.get("ministry_content_patterns") or []
    return tuple(str(p) for p in patterns if str(p).strip())


def ministry_pattern_hits(
    *,
    subject: str = "",
    body: str = "",
    partner: str | None = None,
    sender_email: str = "",
) -> list[str]:
    """Совпадения с ministry_content_patterns в теме, теле, партнёре или домене отправителя."""
    patterns = load_ministry_content_patterns()
    if not patterns:
        return []
    text = normalize_text(f"{subject} {body} {partner or ''}")
    hits = [p for p in patterns if keyword_in_text(p, text)]
    sender_norm = (sender_email or "").lower().strip()
    institution = load_routing_rules().get("institution_operational_director_rules") or {}
    for pattern in institution.get("sender_domain_patterns") or []:
        marker = str(pattern).lower().strip()
        if marker and marker in sender_norm:
            hits.append(marker)
    return hits


def is_ministry_email(
    email: EmailMessage,
    *,
    partner: str | None = None,
) -> bool:
    return bool(
        ministry_pattern_hits(
            subject=email.subject or "",
            body=email.body_text or "",
            partner=partner,
            sender_email=email.sender_email or "",
        )
    )


def check_ministry_not_spam(
    email: EmailMessage,
    *,
    partner: str | None = None,
) -> SpamResult | None:
    """None — не министерство; иначе явный not-spam для обхода всех спам-проверок."""
    hits = ministry_pattern_hits(
        subject=email.subject or "",
        body=email.body_text or "",
        partner=partner,
        sender_email=email.sender_email or "",
    )
    if not hits:
        return None
    return SpamResult(
        is_spam=False,
        confidence=0.0,
        reason=(
            "Письмо министерства/госоргана — не спам, "
            f"маршрут Операционный директор; {', '.join(hits[:5])}"
        ),
        rule_hit="ministry_not_spam",
    )
