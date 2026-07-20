"""Дообучение базы знаний на коррекциях оператора (human-in-the-loop).

Маршрутизация: routing_corrections.json (RuleRouter) + keywords отдела в Qdrant (RAG fallback).
Спам: spam_learning_patterns.json + коллекция spam_learning в Qdrant.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from agent_pochta.routing.corrections import save_routing_correction
from agent_pochta.rules.spam_learning import (
    remove_spam_patterns_by_message_id,
    save_spam_antipattern,
    save_spam_pattern,
)


def collect_department_learning_keywords(correction_entry: dict) -> list[str]:
    """Ключевые слова для обогащения отдела в Qdrant из записи коррекции."""
    seen: set[str] = set()
    result: list[str] = []

    def _add(value: str | None) -> None:
        if not value:
            return
        normalized = value.strip().lower()
        if len(normalized) < 3 or normalized in seen:
            return
        seen.add(normalized)
        result.append(normalized)

    for kw in correction_entry.get("keywords") or []:
        _add(str(kw))

    recipient = correction_entry.get("recipient") or ""
    if "@" in recipient:
        _add(recipient.split("@", 1)[0])

    return result


def enrich_department_in_qdrant(department_id: str, keywords: list[str]) -> dict:
    """Добавляет keywords к отделу в Qdrant (только при RAG_BACKEND=qdrant)."""
    from agent_pochta.config import get_settings

    settings = get_settings()
    if settings.rag_backend != "qdrant":
        return {
            "updated": False,
            "keywords_added": 0,
            "added_keywords": [],
            "reason": "stub_backend",
        }
    if not keywords:
        return {
            "updated": False,
            "keywords_added": 0,
            "added_keywords": [],
            "reason": "no_keywords",
        }
    try:
        from agent_pochta.services.rag_qdrant import append_department_keywords

        return append_department_keywords(settings.qdrant_url, department_id, keywords)
    except Exception as exc:
        return {
            "updated": False,
            "keywords_added": 0,
            "added_keywords": [],
            "reason": f"qdrant_error: {exc}",
        }


def learn_from_routing_correction(
    *,
    message_id: str,
    sender_email: str,
    recipient: str | None,
    subject: str,
    body: str,
    department_id: str,
    department_name: str,
    original_department_id: str | None = None,
    original_department_name: str | None = None,
    path=None,
    session: Session | None = None,
) -> dict:
    """Сохраняет коррекцию и обогащает keywords отдела в Qdrant."""
    entry = save_routing_correction(
        message_id=message_id,
        sender_email=sender_email,
        recipient=recipient,
        subject=subject,
        body=body,
        department_id=department_id,
        department_name=department_name,
        original_department_id=original_department_id,
        original_department_name=original_department_name,
        path=path,
    )
    learning_keywords = collect_department_learning_keywords(entry)
    qdrant = enrich_department_in_qdrant(department_id, learning_keywords)
    if session is None:
        from agent_pochta.stats.change_log import log_routing_correction

        log_routing_correction(
            None,
            message_id=message_id,
            original_department_id=original_department_id,
            original_department_name=original_department_name,
            department_id=department_id,
            department_name=department_name,
            source="learning:routing",
        )
    return {
        "correction_saved": True,
        "correction_id": entry["id"],
        "keywords_added": qdrant["keywords_added"],
        "qdrant_updated": qdrant["updated"],
        "learning_keywords": qdrant.get("added_keywords") or [],
    }


def learn_from_not_spam(
    *,
    message_id: str,
    sender_email: str = "",
    subject: str = "",
    body: str = "",
    reason: str = "Отмечено как не спам",
    path=None,
    session: Session | None = None,
    email_id=None,
) -> dict:
    """Сохраняет not_spam-запись и удаляет spam-запись с тем же message_id."""
    removal = remove_spam_patterns_by_message_id(message_id, path=path)
    antipattern_entry = None
    if sender_email:
        antipattern_entry = save_spam_antipattern(
            message_id=message_id,
            sender_email=sender_email,
            subject=subject,
            body=body,
            reason=reason,
            path=path,
        )
    if session is None:
        from agent_pochta.stats.change_log import log_spam_decision

        log_spam_decision(
            None,
            message_id=message_id,
            email_id=email_id,
            decision="mark_not_spam",
            reason=reason,
            source="learning:not_spam",
        )
    return {
        "spam_pattern_removed": removal["removed_count"] > 0,
        "removed_count": removal["removed_count"],
        "removed_ids": removal["removed_ids"],
        "qdrant_removed": removal["qdrant_removed"],
        "antipattern_saved": antipattern_entry is not None,
        "antipattern_id": antipattern_entry["id"] if antipattern_entry else None,
        "antipattern_qdrant_synced": bool(antipattern_entry.get("qdrant_synced"))
        if antipattern_entry
        else False,
    }


def learn_from_spam_mark(
    *,
    message_id: str,
    sender_email: str,
    subject: str,
    body: str,
    spam_reason: str,
    path=None,
    session: Session | None = None,
    email_id=None,
) -> dict:
    """Сохраняет спам-паттерн (JSON + Qdrant при rag_backend=qdrant)."""
    entry = save_spam_pattern(
        message_id=message_id,
        sender_email=sender_email,
        subject=subject,
        body=body,
        spam_reason=spam_reason,
        path=path,
    )
    if session is None:
        from agent_pochta.stats.change_log import log_spam_decision

        log_spam_decision(
            None,
            message_id=message_id,
            email_id=email_id,
            decision="mark_spam",
            reason=spam_reason,
            source="learning:spam",
        )
    return {
        "spam_pattern_saved": True,
        "spam_pattern_id": entry["id"],
        "qdrant_synced": bool(entry.get("qdrant_synced")),
    }
