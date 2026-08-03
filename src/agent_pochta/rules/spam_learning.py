"""Обучение спам-фильтра: единое хранилище паттернов spam / not_spam.

JSON ``data/spam_learning_patterns.json`` (stub) или коллекция Qdrant ``spam_learning``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from agent_pochta.config import PROJECT_ROOT
from agent_pochta.routing.corrections import extract_spam_learning_keywords
from agent_pochta.schemas import EmailMessage, SpamResult

LearnedEntryLabel = Literal["spam", "not_spam"]
LearnedEntryKind = LearnedEntryLabel

_OPERATOR_SPAM_REASON = "Отмечено офис-менеджером"
_OPERATOR_NOT_SPAM_REASON = "Отмечено как не спам"

_NOT_SPAM_REASON_MARKERS = (
    "не спам",
    "нет признаков спама",
    "не является спамом",
    "деловой запрос",
    "деловая переписка",
    "деловое уведомление",
    "деловое письмо",
    "легитимн",
    "легитимное",
    "не реклам",
    "не фишинг",
    "отсутствуют признаки",
    "отсутствие признаков",
    "ошибка 1с",
    "ошибка odata",
    "нарушение прав доступа",
    "erp",
)

_DEFAULT_LEARNING_PATH = PROJECT_ROOT / "data" / "spam_learning_patterns.json"
SPAM_LEARNING_COLLECTION = "spam_learning"
STORE_VERSION = "2.0"


def resolve_spam_learning_path(path: Path | str | None = None) -> Path:
    if path:
        return Path(path)
    from agent_pochta.config import get_settings

    settings = get_settings()
    custom = settings.spam_learning_path.strip()
    if custom:
        return Path(custom)
    return _DEFAULT_LEARNING_PATH


def _empty_store() -> dict:
    return {"version": STORE_VERSION, "entries": []}


def _normalize_label(raw: str | None) -> LearnedEntryLabel:
    if raw in ("not_spam", "nospam", "antispam"):
        return "not_spam"
    return "spam"


def reason_indicates_not_spam(reason: str | None) -> bool:
    """True, если текст reason явно описывает не-спам (типичный ответ LLM)."""
    text = (reason or "").lower().strip()
    if not text:
        return False
    return any(marker in text for marker in _NOT_SPAM_REASON_MARKERS)


def resolve_human_spam_reason(stored_reason: str | None) -> str:
    """Причина для spam-паттерна после решения оператора mark_spam.

    Не копируем LLM-текст «не спам» или причину эскалации маршрута — только
    явная причина спама или стандартная формулировка оператора.
    """
    from agent_pochta.routing.hitl import is_routing_escalation_reason

    reason = (stored_reason or "").strip()
    if not reason or reason_indicates_not_spam(reason) or is_routing_escalation_reason(reason):
        return _OPERATOR_SPAM_REASON
    return reason


def _reconcile_label_with_reason(label: LearnedEntryLabel, reason: str) -> LearnedEntryLabel:
    if label == "spam" and reason_indicates_not_spam(reason):
        return "not_spam"
    return label


def _normalize_entry(raw: dict) -> dict:
    entry = {k: v for k, v in raw.items() if k != "body_snippet"}
    if "label" in entry:
        entry["label"] = _normalize_label(str(entry["label"]))
    elif entry.get("spam_reason") is not None:
        entry["label"] = "spam"
    else:
        entry["label"] = "not_spam"
    if "reason" not in entry or not str(entry.get("reason") or "").strip():
        legacy_spam = entry.pop("spam_reason", None)
        if legacy_spam:
            entry["reason"] = legacy_spam
        elif entry["label"] == "not_spam":
            entry["reason"] = _OPERATOR_NOT_SPAM_REASON
        else:
            entry["reason"] = _OPERATOR_SPAM_REASON
    entry.pop("spam_reason", None)
    entry["label"] = _reconcile_label_with_reason(
        entry["label"],
        str(entry.get("reason") or ""),
    )
    return entry


def _normalize_store(data: dict) -> dict:
    data.setdefault("version", STORE_VERSION)
    entries = data.get("entries") or []
    data["entries"] = [_normalize_entry(e) for e in entries if isinstance(e, dict)]
    return data


def load_spam_learning(path: Path | str | None = None) -> dict:
    learning_path = resolve_spam_learning_path(path)
    if not learning_path.is_file():
        return _empty_store()
    with learning_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        return _empty_store()
    return _normalize_store(data)


def save_spam_learning(store: dict, path: Path | str | None = None) -> Path:
    learning_path = resolve_spam_learning_path(path)
    learning_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_store(store)
    normalized["version"] = STORE_VERSION
    with learning_path.open("w", encoding="utf-8") as fh:
        json.dump(normalized, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return learning_path


def _upsert_learning_qdrant(entry: dict) -> bool:
    from agent_pochta.config import get_settings

    settings = get_settings()
    if settings.rag_backend != "qdrant":
        return False
    try:
        from agent_pochta.services.spam_learning_rag_qdrant import upsert_spam_learning_entry

        upsert_spam_learning_entry(settings.qdrant_url, entry)
        return True
    except Exception:
        return False


def save_learning_entry(
    *,
    label: LearnedEntryLabel,
    message_id: str,
    sender_email: str,
    subject: str,
    body: str,
    reason: str,
    path: Path | str | None = None,
) -> dict:
    store = load_spam_learning(path)
    normalized_reason = reason.strip() or (
        _OPERATOR_NOT_SPAM_REASON if label == "not_spam" else _OPERATOR_SPAM_REASON
    )
    if label == "spam" and reason_indicates_not_spam(normalized_reason):
        normalized_reason = _OPERATOR_SPAM_REASON
    entry = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message_id": message_id,
        "sender_email": sender_email.lower().strip(),
        "keywords": extract_spam_learning_keywords(subject, body, sender_email=sender_email),
        "label": label,
        "reason": normalized_reason,
    }
    store["entries"].append(entry)
    save_spam_learning(store, path)
    entry["qdrant_synced"] = _upsert_learning_qdrant(entry)
    return entry


def save_spam_pattern(
    *,
    message_id: str,
    sender_email: str,
    subject: str,
    body: str,
    spam_reason: str,
    path: Path | str | None = None,
) -> dict:
    return save_learning_entry(
        label="spam",
        message_id=message_id,
        sender_email=sender_email,
        subject=subject,
        body=body,
        reason=resolve_human_spam_reason(spam_reason),
        path=path,
    )


def save_spam_antipattern(
    *,
    message_id: str,
    sender_email: str,
    subject: str,
    body: str,
    reason: str,
    path: Path | str | None = None,
) -> dict:
    return save_learning_entry(
        label="not_spam",
        message_id=message_id,
        sender_email=sender_email,
        subject=subject,
        body=body,
        reason=reason,
        path=path,
    )


def remove_entries_by_message_id(
    message_id: str,
    *,
    label: LearnedEntryLabel | None = None,
    path: Path | str | None = None,
) -> dict:
    store = load_spam_learning(path)
    entries = store.get("entries") or []
    kept: list[dict] = []
    removed: list[dict] = []
    for entry in entries:
        if (entry.get("message_id") or "") != message_id:
            kept.append(entry)
            continue
        if label is not None and entry.get("label") != label:
            kept.append(entry)
            continue
        removed.append(entry)
    if removed:
        store["entries"] = kept
        save_spam_learning(store, path)

    qdrant_removed = 0
    from agent_pochta.config import get_settings

    settings = get_settings()
    if settings.rag_backend == "qdrant" and removed:
        try:
            from agent_pochta.services.spam_learning_rag_qdrant import (
                delete_spam_learning_by_message_id,
            )

            qdrant_removed = delete_spam_learning_by_message_id(
                settings.qdrant_url,
                message_id,
                label=label,
            )
        except Exception:
            pass

    return {
        "removed_count": len(removed),
        "removed_ids": [e.get("id") for e in removed if e.get("id")],
        "qdrant_removed": qdrant_removed,
    }


def remove_spam_patterns_by_message_id(
    message_id: str,
    path: Path | str | None = None,
) -> dict:
    return remove_entries_by_message_id(message_id, label="spam", path=path)


def remove_spam_antipatterns_by_message_id(
    message_id: str,
    path: Path | str | None = None,
) -> dict:
    return remove_entries_by_message_id(message_id, label="not_spam", path=path)


def _entry_matches_email(
    *,
    sender_email: str,
    subject: str,
    body: str,
    entry: dict,
) -> bool:
    from agent_pochta.routing.normalize import normalize_text

    sender_email = sender_email.lower().strip()
    text = normalize_text(f"{subject} {body}")
    entry_sender = (entry.get("sender_email") or "").lower().strip()
    if entry_sender and entry_sender != sender_email:
        return False
    keywords = entry.get("keywords") or []
    keyword_hits = sum(1 for kw in keywords if kw and kw in text)
    if keywords and keyword_hits == 0 and not entry_sender:
        return False
    score = 0
    if entry_sender:
        score += 3
    score += keyword_hits
    return score > 0


def _collect_learned_entries(*, path: Path | str | None = None) -> list[dict]:
    from agent_pochta.config import get_settings

    settings = get_settings()
    storage_path = resolve_spam_learning_path(path)
    entries: list[dict] = []

    if settings.rag_backend == "qdrant":
        try:
            from agent_pochta.services.spam_learning_rag_qdrant import list_spam_learning_in_qdrant

            entries = list_spam_learning_in_qdrant(settings.qdrant_url)
        except Exception:
            entries = []

    if not entries:
        entries = [
            _normalize_entry(e)
            for e in load_spam_learning(storage_path).get("entries") or []
        ]

    entries.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return entries


def _find_first_learned_match(
    *,
    sender_email: str,
    subject: str,
    body: str,
    path: Path | str | None = None,
) -> tuple[LearnedEntryLabel, dict] | None:
    for entry in _collect_learned_entries(path=path):
        if not _entry_matches_email(
            sender_email=sender_email,
            subject=subject,
            body=body,
            entry=entry,
        ):
            continue
        label = _reconcile_label_with_reason(
            _normalize_label(entry.get("label")),
            str(entry.get("reason") or ""),
        )
        # Противоречивые spam-метки с «легитимным» reason не считаем спамом.
        if label == "spam" and reason_indicates_not_spam(str(entry.get("reason") or "")):
            continue
        return label, entry
    return None


def find_spam_pattern_match(
    *,
    sender_email: str,
    subject: str,
    body: str,
    path: Path | str | None = None,
) -> dict | None:
    from agent_pochta.config import get_settings

    settings = get_settings()
    if settings.rag_backend == "qdrant":
        try:
            from agent_pochta.services.spam_learning_rag_qdrant import find_spam_learning_match

            match = find_spam_learning_match(
                settings.qdrant_url,
                sender_email=sender_email,
                subject=subject,
                body=body,
                label="spam",
            )
            if match is not None:
                return match
        except Exception:
            pass

    for entry in _collect_learned_entries(path=path):
        if entry.get("label") != "spam":
            continue
        if _entry_matches_email(
            sender_email=sender_email,
            subject=subject,
            body=body,
            entry=entry,
        ):
            return entry
    return None


@dataclass
class LearnedSpamDecision:
    """Результат объединённой проверки (первое совпадение по created_at desc)."""

    is_spam: bool
    spam_result: SpamResult | None = None
    matched_entry: dict | None = None
    entry_kind: LearnedEntryKind | None = None


def check_learned_spam_decision(
    email: EmailMessage,
    *,
    path: Path | str | None = None,
) -> LearnedSpamDecision | None:
    match = _find_first_learned_match(
        sender_email=email.sender_email,
        subject=email.subject or "",
        body=email.body_text or "",
        path=path,
    )
    if match is None:
        return None
    label, entry = match
    if label == "not_spam":
        return LearnedSpamDecision(
            is_spam=False,
            matched_entry=entry,
            entry_kind=label,
        )
    reason = entry.get("reason") or "Похожее на ранее отмеченный спам"
    return LearnedSpamDecision(
        is_spam=True,
        spam_result=SpamResult(
            is_spam=True,
            confidence=0.98,
            reason=f"Обучение на спаме: {reason}",
            rule_hit="learned_spam_pattern",
        ),
        matched_entry=entry,
        entry_kind=label,
    )


def resync_spam_learning_to_qdrant(path: Path | str | None = None) -> dict:
    """Пере-записывает все записи из JSON в Qdrant (после ручной правки label/reason)."""
    from agent_pochta.config import get_settings

    settings = get_settings()
    if settings.rag_backend != "qdrant":
        return {"synced": 0, "reason": "stub_backend"}

    store = load_spam_learning(path)
    entries = store.get("entries") or []
    synced = 0
    for entry in entries:
        if _upsert_learning_qdrant(entry):
            synced += 1
    pruned = 0
    try:
        from agent_pochta.services.spam_learning_rag_qdrant import prune_spam_learning_orphans

        valid_ids = {str(e.get("id")) for e in entries if e.get("id")}
        pruned = prune_spam_learning_orphans(settings.qdrant_url, valid_ids)
    except Exception:
        pruned = 0
    return {"synced": synced, "total": len(entries), "pruned": pruned}


def check_learned_spam(email: EmailMessage) -> SpamResult | None:
    decision = check_learned_spam_decision(email)
    if decision is None or not decision.is_spam:
        return None
    return decision.spam_result
