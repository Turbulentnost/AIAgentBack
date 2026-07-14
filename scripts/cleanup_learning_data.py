"""Очистка spam_learning_patterns.json и routing_corrections.json.

Детерминированные правила:
- junk keywords (пунктуация, broken quotes, фрагменты <4 символов, подписи)
- spam reason: sanitize routing HITL / contradictory LLM text
- routing_corrections: migrate + dedupe near-duplicates
- spam entries с routing escalation → удалить (уже в routing_corrections)

Запуск:
  py scripts/cleanup_learning_data.py
  py scripts/cleanup_learning_data.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.routing.corrections import (  # noqa: E402
    _dedupe_substring_keywords,
    _is_useful_keyword,
    _recipient_local_part,
    extract_correction_keywords,
    load_corrections,
    migrate_routing_corrections_store,
    save_corrections,
)
from agent_pochta.routing.hitl import is_routing_escalation_reason  # noqa: E402
from agent_pochta.rules.spam_learning import (  # noqa: E402
    _normalize_entry,
    _reconcile_label_with_reason,
    load_spam_learning,
    reason_indicates_not_spam,
    resolve_human_spam_reason,
    save_spam_learning,
)

_BROKEN_QUOTE_RE = re.compile(r'^["\']|["\']$|[""«»]')
_HTML_ENTITY_RE = re.compile(r"&(?:[a-z]+|#\d+);", re.IGNORECASE)
_LONE_PUNCT_RE = re.compile(r"^[\W_]+$")
_MAILRU_SIGNATURE = frozenset(
    {
        "отправлено",
        "мобильной",
        "почты",
        "mail.ru",
        "mail",
        "https://trk.mail.ru",
        "iphone",
        "pdf",
        "mode",
        "attachment",
    }
)
_MAILRU_PHRASE_MARKERS = (
    "отправлено мобильной",
    "мобильной почты",
    "mode pdf",
    "receipts-renderer",
    "yandex.net",
    "mail.ru",
    "trk.mail.ru",
)
_SIGNATURE_NOISE = frozenset(
    {
        "александра",
        "александр",
        "дмитриева",
        "дмитриев",
        "догадин",
        "виталием.",
        "виталием",
        "здравствуйте,",
        "здравствуйте",
        "коллеги,",
        "коллеги",
        "день!",
        "день!ол",
        "уважаемый",
        "уважаемая",
        "с уважением",
        "attachment",
        "image-03-07-26-09-14.jpeg,",
        "отправлено",
        "мобильной",
        "почты",
        "https",
        "mail",
        "iphone",
        "android",
    }
)
_SHORT_ALLOWLIST = frozenset({"ткп", "упд", "бг", "ол", "бдр", "эдо", "спи"})


def _is_junk_keyword(token: str, *, allow_short: bool = False) -> bool:
    """True если keyword нужно удалить."""
    raw = (token or "").strip()
    if not raw:
        return True
    lower = raw.lower()

    if _HTML_ENTITY_RE.search(raw):
        return True
    if _LONE_PUNCT_RE.match(raw):
        return True
    if raw.startswith(('"', "'")) or raw.endswith(('"', "'")):
        return True

    if lower in _SIGNATURE_NOISE or lower in _MAILRU_SIGNATURE:
        return True

    if any(marker in lower for marker in _MAILRU_PHRASE_MARKERS):
        return True

    if "отправлено" in lower and any(
        m in lower for m in ("mail", "iphone", "android", "attachment", "мобильн", "почты", "pdf")
    ):
        return True

    # short fragments without allowlist
    alnum_len = sum(1 for c in lower if c.isalnum() or ("а" <= c <= "я") or c == "ё")
    if alnum_len < 4 and lower not in _SHORT_ALLOWLIST and not allow_short:
        return False if _is_useful_keyword(lower) else True

    if lower.startswith("http://") or lower.startswith("https://"):
        return True

    if not _is_useful_keyword(lower):
        return True

    # signature name fragments in mixed phrases
    words = lower.split()
    if any(w.rstrip(".,") in _SIGNATURE_NOISE for w in words):
        if len(words) <= 3:
            return True

    return False


def _recompute_spam_keywords(keywords: list[str]) -> list[str]:
    """Пересчитывает keywords через extract_correction_keywords из legacy данных."""
    if not keywords:
        return []
    raw = [str(k).strip() for k in keywords if str(k).strip()]
    if not raw:
        return []

    combined = " ".join(raw)
    subject = raw[0]
    if subject.lower().startswith(("http://", "https://")):
        subject = ""
    body = combined[:500]

    extracted = extract_correction_keywords(subject or combined[:300], body)
    cleaned = _clean_keyword_list(extracted)

    # fallback: сохранить нормализованную тему, если она содержательная
    if not cleaned:
        from agent_pochta.routing.normalize import normalize_text

        for candidate in raw:
            if candidate.lower().startswith(("http://", "https://")):
                continue
            phrase = normalize_text(candidate.strip('"\''))
            if _is_junk_keyword(phrase):
                continue
            if len(phrase) >= 10 and _is_useful_keyword(phrase):
                cleaned = [phrase]
                break
            if len(phrase) >= 6 and " " in phrase and _is_useful_keyword(phrase):
                cleaned = [phrase]
                break

    return _clean_keyword_list(_dedupe_substring_keywords(cleaned))


def _clean_keyword_list(keywords: list[str], *, recipient: str | None = None) -> list[str]:
    local_part = _recipient_local_part(recipient) if recipient else None
    cleaned: list[str] = []
    seen: set[str] = set()
    for kw in keywords or []:
        token = str(kw).strip()
        if not token:
            continue
        lower = token.lower()
        if lower in seen:
            continue
        allow_short = local_part is not None and lower == local_part
        if _is_junk_keyword(token, allow_short=allow_short):
            continue
        seen.add(lower)
        cleaned.append(lower)
    return _dedupe_substring_keywords(cleaned)


def _spam_entry_fingerprint(entry: dict) -> tuple:
    sender = str(entry.get("sender_email") or "").lower().strip()
    label = str(entry.get("label") or "spam")
    kws = tuple(sorted(entry.get("keywords") or []))
    # если keywords пусты — различать по message_id
    if not kws:
        return sender, label, str(entry.get("message_id") or entry.get("id") or "")
    return sender, label, kws


def _routing_entry_fingerprint(entry: dict) -> tuple:
    sender = str(entry.get("sender_email") or "").lower().strip()
    recipient = str(entry.get("recipient") or "").lower().strip()
    dept = str(entry.get("department_id") or "")
    subject = str(entry.get("subject") or "").lower().strip()
    return sender, recipient, dept, subject


def cleanup_spam_learning(*, dry_run: bool = False) -> dict:
    store = load_spam_learning()
    entries = list(store.get("entries") or [])
    stats = {
        "entries_before": len(entries),
        "entries_removed": 0,
        "entries_after": 0,
        "keywords_before": 0,
        "keywords_removed": 0,
        "keywords_after": 0,
        "reasons_fixed": 0,
        "labels_reconciled": 0,
        "removed_routing_escalation": [],
        "removed_duplicates": [],
        "removed_empty": [],
        "sample_removed_keywords": [],
    }

    cleaned_entries: list[dict] = []
    seen_fingerprints: dict[tuple, str] = {}

    for entry in entries:
        eid = str(entry.get("id") or "")
        reason = str(entry.get("reason") or "")

        # spam entries that are actually routing corrections
        if is_routing_escalation_reason(reason):
            stats["entries_removed"] += 1
            stats["removed_routing_escalation"].append(eid)
            continue

        normalized = _normalize_entry(dict(entry))
        old_label = entry.get("label")
        new_label = normalized.get("label")
        if old_label != new_label:
            stats["labels_reconciled"] += 1

        old_reason = str(entry.get("reason") or "")
        if normalized.get("label") == "spam":
            fixed_reason = resolve_human_spam_reason(old_reason)
            if fixed_reason != old_reason:
                stats["reasons_fixed"] += 1
            normalized["reason"] = fixed_reason
        elif reason_indicates_not_spam(old_reason) and normalized.get("label") == "not_spam":
            # keep descriptive not_spam reasons; only strip routing text
            if is_routing_escalation_reason(old_reason):
                normalized["reason"] = "Отмечено как не спам"
                stats["reasons_fixed"] += 1

        # reconcile again after reason fix
        normalized["label"] = _reconcile_label_with_reason(
            normalized["label"],
            str(normalized.get("reason") or ""),
        )

        old_kws = list(normalized.get("keywords") or [])
        stats["keywords_before"] += len(old_kws)
        new_kws = _recompute_spam_keywords(old_kws)
        stats["keywords_removed"] += len(old_kws) - len(new_kws)
        if len(old_kws) > len(new_kws) and len(stats["sample_removed_keywords"]) < 20:
            removed = set(k.lower() for k in old_kws) - set(k.lower() for k in new_kws)
            stats["sample_removed_keywords"].extend(sorted(removed)[:5])
        normalized["keywords"] = new_kws
        stats["keywords_after"] += len(new_kws)

        if not new_kws and not normalized.get("sender_email"):
            stats["entries_removed"] += 1
            stats["removed_empty"].append(eid)
            continue

        fp = _spam_entry_fingerprint(normalized)
        if fp in seen_fingerprints:
            stats["entries_removed"] += 1
            stats["removed_duplicates"].append(eid)
            continue
        seen_fingerprints[fp] = eid
        cleaned_entries.append(normalized)

    stats["entries_after"] = len(cleaned_entries)
    stats["sample_removed_keywords"] = list(dict.fromkeys(stats["sample_removed_keywords"]))[:25]

    if not dry_run:
        store["entries"] = cleaned_entries
        save_spam_learning(store)

    return stats


def cleanup_routing_corrections(*, dry_run: bool = False) -> dict:
    path = ROOT / "data" / "routing_corrections.json"
    store = load_corrections(path)
    entries_before = list(store.get("entries") or [])
    kw_before = sum(len(e.get("keywords") or []) for e in entries_before)

    migrate_stats = migrate_routing_corrections_store(path, dry_run=dry_run)
    if dry_run:
        # simulate migrate in memory
        from agent_pochta.routing.corrections import (
            _clean_token,
            _entry_source_text,
            _strip_subject_prefix,
        )
        from agent_pochta.routing.normalize import normalize_text

        migrated: list[dict] = []
        for entry in entries_before:
            m = dict(entry)
            m.pop("message_id", None)
            subject, body = _entry_source_text(m)
            subject_clean = _strip_subject_prefix(subject)
            if subject_clean:
                m["subject"] = normalize_text(_clean_token(subject_clean))
            m.pop("body", None)
            m["keywords"] = extract_correction_keywords(
                subject,
                body,
                recipient=m.get("recipient"),
                department_id=m.get("department_id"),
                corpus_entries=entries_before,
            )
            migrated.append(m)
        entries = migrated
    else:
        store = load_corrections(path)
        entries = list(store.get("entries") or [])

    stats = {
        "entries_before": len(entries_before),
        "entries_removed": 0,
        "entries_after": 0,
        "keywords_before": kw_before,
        "keywords_removed": 0,
        "keywords_after": 0,
        "message_ids_removed": migrate_stats.get("message_ids_removed", 0),
        "removed_duplicates": [],
        "sample_removed_keywords": [],
    }

    seen: dict[tuple, str] = {}
    deduped: list[dict] = []
    for entry in entries:
        eid = str(entry.get("id") or "")
        fp = _routing_entry_fingerprint(entry)
        if fp in seen:
            stats["entries_removed"] += 1
            stats["removed_duplicates"].append(eid)
            continue
        seen[fp] = eid
        deduped.append(entry)

    stats["entries_after"] = len(deduped)
    stats["keywords_after"] = sum(len(e.get("keywords") or []) for e in deduped)
    stats["keywords_removed"] = stats["keywords_before"] - stats["keywords_after"]

    # collect sample of junk that migrate removed
    before_kw = {k.lower() for e in entries_before for k in (e.get("keywords") or [])}
    after_kw = {k.lower() for e in deduped for k in (e.get("keywords") or [])}
    removed = before_kw - after_kw
    junk_samples = sorted(
        k
        for k in removed
        if _is_junk_keyword(k) or k.startswith('"') or k.endswith('"') or k.endswith(",")
    )
    stats["sample_removed_keywords"] = junk_samples[:25]

    if not dry_run:
        store["entries"] = deduped
        save_corrections(store, path)

    return stats


def backup_files() -> Path:
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    backup_dir = ROOT / "backup" / f"{ts}_pre_cleanup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in ("spam_learning_patterns.json", "routing_corrections.json"):
        src = ROOT / "data" / name
        if src.is_file():
            shutil.copy2(src, backup_dir / name)
    return backup_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Cleanup learning JSON data files")
    parser.add_argument("--dry-run", action="store_true", help="Only report, do not write")
    args = parser.parse_args()

    backup_dir = backup_files()
    print(f"Backup: {backup_dir}")

    spam_stats = cleanup_spam_learning(dry_run=args.dry_run)
    routing_stats = cleanup_routing_corrections(dry_run=args.dry_run)

    log = {
        "timestamp": datetime.now().isoformat(),
        "dry_run": args.dry_run,
        "backup_dir": str(backup_dir),
        "spam_learning": spam_stats,
        "routing_corrections": routing_stats,
    }

    log_path = ROOT / "data" / "learning_data_cleanup_log.json"
    if not args.dry_run:
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\n=== spam_learning_patterns.json ===")
    print(
        f"entries: {spam_stats['entries_before']} → {spam_stats['entries_after']} "
        f"(removed {spam_stats['entries_removed']})"
    )
    print(
        f"keywords: {spam_stats['keywords_before']} → {spam_stats['keywords_after']} "
        f"(removed {spam_stats['keywords_removed']})"
    )
    print(f"reasons fixed: {spam_stats['reasons_fixed']}, labels reconciled: {spam_stats['labels_reconciled']}")
    if spam_stats["sample_removed_keywords"]:
        print("sample removed keywords:", ", ".join(spam_stats["sample_removed_keywords"][:10]))

    print("\n=== routing_corrections.json ===")
    print(
        f"entries: {routing_stats['entries_before']} → {routing_stats['entries_after']} "
        f"(removed {routing_stats['entries_removed']})"
    )
    print(
        f"keywords: {routing_stats['keywords_before']} → {routing_stats['keywords_after']} "
        f"(removed {routing_stats['keywords_removed']})"
    )
    if routing_stats["sample_removed_keywords"]:
        print("sample removed keywords:", ", ".join(routing_stats["sample_removed_keywords"][:10]))

    if not args.dry_run:
        print(f"\nLog written: {log_path}")


if __name__ == "__main__":
    main()
