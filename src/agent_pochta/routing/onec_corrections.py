"""Коррекции полей 1С (партнёр / организация) в routing_rules.json + Qdrant.

Секция ``onec_corrections`` не используется RuleRouter для маршрутизации отделов —
только для обучения партнёра и организации после HITL «Сохранить изменения».
При ``RAG_BACKEND=qdrant`` записи сразу upsertятся в коллекцию ``onec_corrections``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent_pochta.routing.corrections import extract_correction_keywords
from agent_pochta.routing.normalize import normalize_email_address, normalize_text
from agent_pochta.routing.organizations import normalize_organization_code
from agent_pochta.services.llm_analyze import normalize_partner_name
from agent_pochta.services.onec_corrections_rag_qdrant import (
    ONEC_CORRECTIONS_COLLECTION,
)
from agent_pochta.services.routing_departments import load_routing_rules, resolve_routing_rules_path

_ONEC_KEY = "onec_corrections"

__all__ = [
    "ONEC_CORRECTIONS_COLLECTION",
    "empty_onec_corrections",
    "ensure_onec_corrections_section",
    "find_onec_correction_match",
    "load_onec_corrections",
    "resync_onec_corrections_to_qdrant",
    "save_onec_correction",
]


def empty_onec_corrections() -> dict:
    return {"version": "1.0", "entries": []}


def ensure_onec_corrections_section(rules: dict) -> dict:
    """Гарантирует наличие валидной секции onec_corrections в dict правил."""
    section = rules.get(_ONEC_KEY)
    if not isinstance(section, dict):
        rules[_ONEC_KEY] = empty_onec_corrections()
        return rules[_ONEC_KEY]
    section.setdefault("version", "1.0")
    if not isinstance(section.get("entries"), list):
        section["entries"] = []
    return section


def load_onec_corrections(path: Path | str | None = None) -> dict:
    rules = load_routing_rules(path)
    return dict(ensure_onec_corrections_section(rules))


def _save_rules(rules: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(rules, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def save_onec_correction(
    *,
    partner: str | None = None,
    organization: str | None = None,
    sender_email: str = "",
    recipient: str | None = None,
    subject: str = "",
    body: str = "",
    department_id: str | None = None,
    department_name: str | None = None,
    path: Path | str | None = None,
) -> dict | None:
    """Записывает коррекцию партнёра/организации. None, если оба поля пусты."""
    partner_norm = normalize_partner_name(partner)
    org_norm = normalize_organization_code(organization)
    if not partner_norm and not org_norm:
        return None

    rules_path = resolve_routing_rules_path(path)
    rules = load_routing_rules(rules_path)
    section = ensure_onec_corrections_section(rules)
    aliases = rules.get("email_aliases")
    normalized_recipient = (
        normalize_email_address(recipient or "", aliases) if recipient else None
    )
    corpus = list(section.get("entries") or [])
    entry = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "partner": partner_norm,
        "organization": org_norm,
        "department_id": department_id,
        "department_name": department_name,
        "sender_email": (sender_email or "").lower().strip(),
        "recipient": normalized_recipient,
        "subject": normalize_text(subject) if subject else "",
        "keywords": extract_correction_keywords(
            subject,
            body,
            recipient=normalized_recipient,
            department_id=department_id,
            corpus_entries=corpus,
        ),
    }
    section["entries"].append(entry)
    rules[_ONEC_KEY] = section
    _save_rules(rules, rules_path)
    entry["qdrant_synced"] = _upsert_onec_qdrant(entry)

    from agent_pochta.routing.engine import reset_route_engine

    reset_route_engine()
    return entry


def _upsert_onec_qdrant(entry: dict) -> bool:
    from agent_pochta.config import get_settings

    settings = get_settings()
    if settings.rag_backend != "qdrant":
        return False
    try:
        from agent_pochta.services.onec_corrections_rag_qdrant import (
            upsert_onec_correction_entry,
        )

        upsert_onec_correction_entry(settings.qdrant_url, entry)
        return True
    except Exception:
        return False


def _collect_onec_entries(path: Path | str | None = None) -> list[dict]:
    """Предпочитает Qdrant при rag_backend=qdrant, иначе JSON."""
    from agent_pochta.config import get_settings

    settings = get_settings()
    if settings.rag_backend == "qdrant":
        try:
            from agent_pochta.services.onec_corrections_rag_qdrant import (
                list_onec_corrections_in_qdrant,
            )

            entries = list_onec_corrections_in_qdrant(settings.qdrant_url)
            if entries:
                return entries
        except Exception:
            pass
    return list(load_onec_corrections(path).get("entries") or [])


def resync_onec_corrections_to_qdrant(path: Path | str | None = None) -> dict:
    """Полный re-upsert JSON → Qdrant + prune orphan-точек."""
    from agent_pochta.config import get_settings

    settings = get_settings()
    if settings.rag_backend != "qdrant":
        return {"synced": 0, "total": 0, "pruned": 0, "reason": "stub_backend"}

    from agent_pochta.services.onec_corrections_rag_qdrant import (
        ensure_onec_corrections_indexes,
        prune_onec_corrections_orphans,
        upsert_onec_correction_entry,
    )

    ensure_onec_corrections_indexes(settings.qdrant_url)
    entries = list(load_onec_corrections(path).get("entries") or [])
    synced = 0
    for entry in entries:
        if not entry.get("id"):
            continue
        try:
            upsert_onec_correction_entry(settings.qdrant_url, entry)
            synced += 1
        except Exception:
            continue
    valid_ids = {str(entry["id"]) for entry in entries if entry.get("id")}
    pruned = prune_onec_corrections_orphans(settings.qdrant_url, valid_ids)
    return {"synced": synced, "total": len(entries), "pruned": pruned}


def find_onec_correction_match(
    *,
    recipient: str,
    sender_email: str,
    subject: str,
    body: str,
    path: Path | str | None = None,
) -> dict | None:
    """Ищет коррекцию партнёра/организации по sender / recipient / keywords."""
    entries = _collect_onec_entries(path)
    if not entries:
        return None

    rules = load_routing_rules(path)
    recipient = normalize_email_address(recipient, rules.get("email_aliases"))
    sender_email = sender_email.lower().strip()
    text = normalize_text(f"{subject} {body}")

    best: tuple[int, dict] | None = None
    for entry in reversed(entries):
        if not entry.get("partner") and not entry.get("organization"):
            continue
        score = 0
        entry_recipient = entry.get("recipient")
        if entry_recipient:
            if entry_recipient != recipient:
                continue
            score += 3
        entry_sender = (entry.get("sender_email") or "").lower().strip()
        if entry_sender:
            if entry_sender != sender_email:
                continue
            score += 2
        keywords = entry.get("keywords") or []
        keyword_hits = sum(1 for kw in keywords if kw and kw in text)
        if keywords and keyword_hits == 0 and not entry_recipient and not entry_sender:
            continue
        score += keyword_hits
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, entry)

    return best[1] if best else None
