"""Расчёт уровня уверенности (ТЗ §11)."""

from __future__ import annotations

from agent_pochta.routing.models import ConfidenceLevel


def score_to_level(score: int) -> ConfidenceLevel:
    if score >= 80:
        return ConfidenceLevel.HIGH
    if score >= 50:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


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
    score = 0
    if exact_email:
        score += 70
    score += min(topic_matches * 10, 20)
    if email_keyword:
        score += 45
    score += min(content_keyword_hits * 5, 25)
    if org_confirmed:
        score += 10
    if holding_found:
        score += 15
    if has_conflict:
        score -= 25
    if info_mailbox_no_topic:
        score -= 20
    if unknown_route:
        score -= 40
    score = max(0, min(100, score))
    return score, score_to_level(score)
