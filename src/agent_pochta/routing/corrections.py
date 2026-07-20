"""Сохранение и применение коррекций маршрутизации (human-in-the-loop).

Запись в routing_corrections.json; дообучение Qdrant — routing.learning.learn_from_routing_correction.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent_pochta.config import PROJECT_ROOT
from agent_pochta.routing.normalize import normalize_email_address, normalize_text
from agent_pochta.services.routing_departments import load_routing_rules

_DEFAULT_CORRECTIONS_PATH = PROJECT_ROOT / "data" / "routing_corrections.json"
_STOPWORDS = {
    "этот",
    "этого",
    "который",
    "которые",
    "письмо",
    "просьба",
    "здравствуйте",
    "добрый",
    "день",
    "уважаем",
    "please",
    "hello",
    "dear",
    "the",
    "and",
    "for",
    "from",
    "with",
}


def resolve_corrections_path(path: Path | str | None = None) -> Path:
    if path:
        return Path(path)
    from agent_pochta.config import get_settings

    custom = get_settings().routing_corrections_path.strip()
    if custom:
        return Path(custom)
    return _DEFAULT_CORRECTIONS_PATH


def _empty_store() -> dict:
    return {"version": "1.0", "entries": []}


def load_corrections(path: Path | str | None = None) -> dict:
    corrections_path = resolve_corrections_path(path)
    if not corrections_path.is_file():
        return _empty_store()
    with corrections_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        return _empty_store()
    data.setdefault("version", "1.0")
    data.setdefault("entries", [])
    return data


def save_corrections(store: dict, path: Path | str | None = None) -> Path:
    corrections_path = resolve_corrections_path(path)
    corrections_path.parent.mkdir(parents=True, exist_ok=True)
    with corrections_path.open("w", encoding="utf-8") as fh:
        json.dump(store, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return corrections_path


def extract_correction_keywords(subject: str, body: str, *, limit: int = 8) -> list[str]:
    text = normalize_text(f"{subject} {body[:500]}")
    keywords: list[str] = []
    subject_norm = normalize_text(subject)
    if subject_norm and len(subject_norm) >= 4:
        keywords.append(subject_norm)
    for token in text.split():
        if len(token) < 4 or token in _STOPWORDS:
            continue
        if token not in keywords:
            keywords.append(token)
        if len(keywords) >= limit:
            break
    return keywords


def save_routing_correction(
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
    path: Path | str | None = None,
) -> dict:
    store = load_corrections(path)
    rules = load_routing_rules()
    normalized_recipient = (
        normalize_email_address(recipient or "", rules.get("email_aliases")) if recipient else None
    )
    entry = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message_id": message_id,
        "sender_email": sender_email.lower().strip(),
        "recipient": normalized_recipient,
        "keywords": extract_correction_keywords(subject, body),
        "department_id": department_id,
        "department_name": department_name,
        "original_department_id": original_department_id,
        "original_department_name": original_department_name,
    }
    store["entries"].append(entry)
    save_corrections(store, path)
    from agent_pochta.routing.engine import reset_route_engine

    reset_route_engine()
    return entry


def find_correction_match(
    *,
    recipient: str,
    sender_email: str,
    subject: str,
    body: str,
    path: Path | str | None = None,
) -> dict | None:
    store = load_corrections(path)
    entries = store.get("entries") or []
    if not entries:
        return None

    rules = load_routing_rules()
    recipient = normalize_email_address(recipient, rules.get("email_aliases"))
    sender_email = sender_email.lower().strip()
    text = normalize_text(f"{subject} {body}")

    best: tuple[int, dict] | None = None
    for entry in reversed(entries):
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
