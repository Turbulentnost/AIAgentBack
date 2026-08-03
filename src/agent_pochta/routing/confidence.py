"""Расчёт уровня уверенности (ТЗ §11 + балльная модель evidence)."""

from __future__ import annotations

from agent_pochta.routing.evidence import (
    accumulate_confidence,
    evaluate_route_confidence,
    score_to_level,
)
from agent_pochta.routing.models import ConfidenceLevel

# Обратная совместимость: старый API calculate_confidence → evidence.
def calculate_confidence(
    *,
    exact_email: bool = False,
    topic_matches: int = 0,
    email_keyword: bool = False,
    content_keyword_hits: int = 0,
    org_confirmed: bool = False,
    holding_found: bool = False,
    has_conflict: bool = False,
    info_mailbox_no_topic: bool = False,
    unknown_route: bool = False,
) -> tuple[int, ConfidenceLevel]:
    """Legacy wrapper: эмулирует старые флаги через evaluate_route_confidence."""
    if exact_email:
        source = "exact_email"
    elif email_keyword:
        source = "email_keyword"
    elif holding_found:
        source = "det_sales_gazprom"
    elif content_keyword_hits:
        source = "content"
    elif unknown_route:
        source = "reserve"
    else:
        source = "content"

    result = evaluate_route_confidence(
        match_source=source,
        department_code="",
        topic_hits=topic_matches,
        content_hits=content_keyword_hits,
        org_confirmed=org_confirmed,
        has_conflict=has_conflict,
        info_mailbox_no_topic=info_mailbox_no_topic,
        unknown_route=unknown_route,
        apply_floor=False,
    )
    return result.score, result.level


__all__ = [
    "accumulate_confidence",
    "calculate_confidence",
    "evaluate_route_confidence",
    "score_to_level",
    "ConfidenceLevel",
]
