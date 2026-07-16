"""Дообучение базы знаний на коррекциях оператора (human-in-the-loop).

Маршрутизация: routing_corrections.json (RuleRouter) + keywords отдела в Qdrant (RAG fallback).
Спам: spam_learning_patterns.json + коллекция spam_learning в Qdrant.
Партнёр/организация: onec_corrections в routing_rules.json + коллекция onec_corrections.
HITL-контрагент: PostgreSQL + коллекция contractors.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from agent_pochta.routing.corrections import (
    _correction_fingerprint,
    _is_useful_keyword,
    extract_correction_keywords,
    load_corrections,
    save_corrections,
    save_routing_correction,
)
from agent_pochta.rules.spam_learning import (
    load_spam_learning,
    remove_spam_patterns_by_message_id,
    save_spam_antipattern,
    save_spam_learning,
    save_spam_pattern,
)
from agent_pochta.schemas import Contractor


def collect_department_learning_keywords(correction_entry: dict) -> list[str]:
    """Ключевые слова для обогащения отдела в Qdrant из записи коррекции."""
    seen: set[str] = set()
    result: list[str] = []

    def _add(value: str | None) -> None:
        if not value:
            return
        normalized = value.strip().lower()
        if not _is_useful_keyword(normalized):
            return
        if normalized in seen:
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


def enrich_hitl_contractor_in_qdrant(
    *,
    contractor_id: str,
    name: str,
    email: str,
    department_code: str | None = None,
    contractor_type: str = "клиент",
) -> dict:
    """Upsert HITL-контрагента в коллекцию contractors (только при RAG_BACKEND=qdrant)."""
    from agent_pochta.config import get_settings

    settings = get_settings()
    if settings.rag_backend != "qdrant":
        return {"upserted": 0, "reason": "stub_backend"}
    email_norm = (email or "").lower().strip()
    if not email_norm or not name:
        return {"upserted": 0, "reason": "missing_email_or_name"}
    try:
        from agent_pochta.services.rag_qdrant import upsert_contractors_merge

        contractor = Contractor(
            contractor_id=contractor_id,
            name=name,
            emails=[email_norm],
            department_codes=[department_code] if department_code else [],
            contractor_type=contractor_type or "клиент",
        )
        upserted = upsert_contractors_merge(settings.qdrant_url, [contractor])
        return {"upserted": upserted, "email": email_norm, "contractor_id": contractor_id}
    except Exception as exc:
        return {"upserted": 0, "reason": f"qdrant_error: {exc}"}


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
    partner: str | None = None,
    organization: str | None = None,
    path=None,
    session: Session | None = None,
    routing_rules_path=None,
) -> dict:
    """Сохраняет коррекцию отдела и (при наличии) полей 1С партнёр/организация."""
    entry = save_routing_correction(
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

    onec_entry = None
    if partner or organization:
        from agent_pochta.routing.onec_corrections import save_onec_correction

        onec_entry = save_onec_correction(
            partner=partner,
            organization=organization,
            sender_email=sender_email,
            recipient=recipient,
            subject=subject,
            body=body,
            department_id=department_id,
            department_name=department_name,
            path=routing_rules_path,
        )

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
    result = {
        "correction_saved": True,
        "correction_id": entry["id"],
        "keywords_added": qdrant["keywords_added"],
        "qdrant_updated": qdrant["updated"],
        "learning_keywords": qdrant.get("added_keywords") or [],
    }
    if onec_entry is not None:
        result["onec_correction_saved"] = True
        result["onec_correction_id"] = onec_entry["id"]
        result["onec_partner"] = onec_entry.get("partner")
        result["onec_organization"] = onec_entry.get("organization")
        result["onec_qdrant_synced"] = bool(onec_entry.get("qdrant_synced"))
    return result


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


def _should_migrate_spam_entry(
    entry: dict,
    *,
    correction_fingerprints: set[tuple[str, str, str]],
) -> bool:
    from agent_pochta.routing.hitl import (
        is_routing_escalation_reason,
        parse_recipient_from_message_id,
        resolve_department_from_recipient,
    )

    message_id = str(entry.get("message_id") or "")
    reason = str(entry.get("reason") or "")

    if is_routing_escalation_reason(reason):
        return True

    recipient = parse_recipient_from_message_id(message_id)
    department = resolve_department_from_recipient(recipient) if recipient else None
    if department is None:
        return False
    fingerprint = (
        str(entry.get("sender_email") or "").lower().strip(),
        str(recipient or "").lower().strip(),
        department[0],
    )
    return fingerprint in correction_fingerprints


def migrate_misrouted_spam_entries(
    *,
    spam_path=None,
    corrections_path=None,
    since: str | None = "2026-07-09",
    dry_run: bool = False,
) -> dict:
    """Переносит ошибочные записи из spam_learning в routing (departments Qdrant path).

    Удаляет из spam_learning записи с причинами маршрутизации и дубликаты,
    для которых уже есть routing_corrections или можно вывести отдел по recipient.
    """
    from agent_pochta.routing.hitl import (
        parse_recipient_from_message_id,
        resolve_department_from_recipient,
    )

    corrections_store = load_corrections(corrections_path)
    correction_entries = list(corrections_store.get("entries") or [])
    correction_fingerprints = {_correction_fingerprint(entry) for entry in correction_entries}

    spam_store = load_spam_learning(spam_path)
    kept: list[dict] = []
    removed: list[dict] = []
    migrated: list[dict] = []

    for entry in spam_store.get("entries") or []:
        created_at = str(entry.get("created_at") or "")
        if since and created_at and created_at[:10] < since:
            kept.append(entry)
            continue
        if not _should_migrate_spam_entry(entry, correction_fingerprints=correction_fingerprints):
            kept.append(entry)
            continue
        removed.append(entry)

    for entry in removed:
        message_id = str(entry.get("message_id") or "")
        recipient = parse_recipient_from_message_id(message_id)
        department = resolve_department_from_recipient(recipient) if recipient else None
        if department is None:
            continue

        department_id, department_name = department
        fingerprint = (
            str(entry.get("sender_email") or "").lower().strip(),
            str(recipient or "").lower().strip(),
            department_id,
        )
        if fingerprint in correction_fingerprints:
            continue

        subject_hint = str(entry.get("subject") or "")
        body_hint = str(entry.get("body") or "")
        if not subject_hint:
            keywords = entry.get("keywords") or []
            subject_hint = str(keywords[0]) if keywords else ""
            body_hint = " ".join(str(kw) for kw in keywords[1:3] if kw)

        migrated_entry = {
            "id": str(uuid.uuid4()),
            "created_at": entry.get("created_at") or datetime.now(timezone.utc).isoformat(),
            "sender_email": str(entry.get("sender_email") or "").lower().strip(),
            "recipient": recipient,
            "subject": subject_hint,
            "keywords": extract_correction_keywords(
                subject_hint,
                body_hint,
                recipient=recipient,
                department_id=department_id,
                corpus_entries=correction_entries,
            ),
            "department_id": department_id,
            "department_name": department_name,
            "original_department_id": department_id,
            "original_department_name": department_name,
            "migrated_from_spam_learning_id": entry.get("id"),
            "migration_reason": entry.get("reason"),
        }
        correction_entries.append(migrated_entry)
        correction_fingerprints.add(fingerprint)
        migrated.append(migrated_entry)

    if dry_run:
        return {
            "removed_count": len(removed),
            "migrated_count": len(migrated),
            "kept_count": len(kept),
            "removed_ids": [entry.get("id") for entry in removed if entry.get("id")],
            "migrated_fingerprints": [
                _correction_fingerprint(entry) for entry in migrated
            ],
            "dry_run": True,
        }

    if removed:
        spam_store["entries"] = kept
        save_spam_learning(spam_store, spam_path)
        from agent_pochta.rules.spam_learning import resync_spam_learning_to_qdrant

        resync_spam_learning_to_qdrant(spam_path)

    if migrated:
        corrections_store["entries"] = correction_entries
        save_corrections(corrections_store, corrections_path)
        for migrated_entry in migrated:
            enrich_department_in_qdrant(
                migrated_entry["department_id"],
                collect_department_learning_keywords(migrated_entry),
            )

    return {
        "removed_count": len(removed),
        "migrated_count": len(migrated),
        "kept_count": len(kept),
        "removed_ids": [entry.get("id") for entry in removed if entry.get("id")],
        "migrated_fingerprints": [_correction_fingerprint(entry) for entry in migrated],
        "dry_run": False,
    }
