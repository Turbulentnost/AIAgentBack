"""Загрузка базовых not_spam-паттернов в spam_learning_patterns.json.

Запуск:
  python scripts/seed_spam_learning_not_spam.py              # dry-run
  python scripts/seed_spam_learning_not_spam.py --apply      # записать JSON
  python scripts/seed_spam_learning_not_spam.py --apply --qdrant  # + Qdrant
  python scripts/seed_spam_learning_not_spam.py --apply --force   # обновить seed-записи
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings, reset_settings  # noqa: E402
from agent_pochta.rules.spam_learning import (  # noqa: E402
    load_spam_learning,
    resolve_spam_learning_path,
    save_spam_learning,
)

SEED_PATH = ROOT / "data" / "spam_learning_not_spam_seed.json"
SEED_PREFIX = "seed-not-spam-"
SEED_NAMESPACE = uuid.UUID("a3f2c8e1-4b5d-6e7f-8091-a2b3c4d5e6f7")


def load_seed_categories(path: Path | None = None) -> list[dict]:
    seed_file = path or SEED_PATH
    with seed_file.open(encoding="utf-8") as fh:
        data = json.load(fh)
    categories = data.get("categories") or []
    if not isinstance(categories, list):
        raise ValueError("categories must be a list")
    return [c for c in categories if isinstance(c, dict)]


def _seed_entry_id(message_id: str) -> str:
    return str(uuid.uuid5(SEED_NAMESPACE, message_id))


def build_seed_entries(
    categories: list[dict],
    *,
    created_at: str | None = None,
) -> list[dict]:
    ts = created_at or datetime.now(timezone.utc).isoformat()
    entries: list[dict] = []
    for cat in categories:
        message_id = str(cat.get("message_id") or "").strip()
        if not message_id.startswith(SEED_PREFIX):
            raise ValueError(f"message_id must start with {SEED_PREFIX!r}: {message_id!r}")
        keywords = [str(kw).strip().lower() for kw in (cat.get("keywords") or []) if str(kw).strip()]
        if not keywords:
            raise ValueError(f"empty keywords for {message_id}")
        reason = str(cat.get("reason") or "Деловая переписка (базовое обучение)").strip()
        entries.append(
            {
                "id": _seed_entry_id(message_id),
                "created_at": ts,
                "message_id": message_id,
                "sender_email": "",
                "keywords": keywords,
                "label": "not_spam",
                "reason": reason,
            }
        )
    return entries


def merge_seed_entries(
    store: dict,
    seed_entries: list[dict],
    *,
    force: bool = False,
) -> tuple[list[dict], int, int]:
    """Вернуть (новый store, added, updated)."""
    entries = list(store.get("entries") or [])
    by_message_id = {e.get("message_id"): i for i, e in enumerate(entries) if e.get("message_id")}

    added = 0
    updated = 0
    for seed in seed_entries:
        mid = seed["message_id"]
        if mid in by_message_id:
            if not force:
                continue
            idx = by_message_id[mid]
            entries[idx] = seed
            updated += 1
        else:
            entries.append(seed)
            by_message_id[mid] = len(entries) - 1
            added += 1

    store["entries"] = entries
    return store, added, updated


def sync_qdrant(entries: list[dict]) -> int:
    settings = get_settings()
    if settings.rag_backend != "qdrant":
        print("RAG_BACKEND != qdrant — пропуск Qdrant")
        return 0
    from agent_pochta.services.spam_learning_rag_qdrant import upsert_spam_learning_entry

    synced = 0
    for entry in entries:
        try:
            upsert_spam_learning_entry(settings.qdrant_url, entry)
            synced += 1
        except Exception as exc:
            print(f"  ! Qdrant upsert failed for {entry.get('message_id')}: {exc}")
    return synced


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed not_spam business patterns for spam learning")
    parser.add_argument("--apply", action="store_true", help="Записать в spam_learning_patterns.json")
    parser.add_argument("--force", action="store_true", help="Обновить существующие seed-not-spam-* записи")
    parser.add_argument("--qdrant", action="store_true", help="Upsert в Qdrant (RAG_BACKEND=qdrant)")
    parser.add_argument("--seed", type=Path, default=SEED_PATH, help="Путь к seed JSON")
    parser.add_argument("--learning-path", type=Path, default=None, help="Путь к spam_learning_patterns.json")
    args = parser.parse_args()

    reset_settings()
    categories = load_seed_categories(args.seed)
    seed_entries = build_seed_entries(categories)
    learning_path = resolve_spam_learning_path(args.learning_path)
    store = load_spam_learning(learning_path)
    store, added, updated = merge_seed_entries(store, seed_entries, force=args.force)

    not_spam_total = sum(1 for e in store["entries"] if e.get("label") == "not_spam")
    seed_in_store = [e for e in store["entries"] if str(e.get("message_id", "")).startswith(SEED_PREFIX)]

    print(f"Seed-категорий в файле: {len(categories)}")
    print(f"Будет добавлено: {added}, обновлено: {updated}")
    print(f"not_spam в хранилище после merge: {not_spam_total} (seed: {len(seed_in_store)})")
    print()
    for entry in seed_entries:
        kws = ", ".join(entry["keywords"][:4])
        if len(entry["keywords"]) > 4:
            kws += f", … (+{len(entry['keywords']) - 4})"
        print(f"  • {entry['message_id']}: [{kws}]")

    if not args.apply:
        print("\nДобавьте --apply для записи.")
        return

    save_spam_learning(store, learning_path)
    print(f"\nЗаписано: {learning_path}")

    if args.qdrant:
        synced = sync_qdrant(seed_entries)
        print(f"Qdrant upsert: {synced}/{len(seed_entries)}")


if __name__ == "__main__":
    main()
