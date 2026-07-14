"""Синхронизация локальных JSON / PostgreSQL → Qdrant.

Примеры:
  python scripts/sync_rag_to_qdrant.py
  python scripts/sync_rag_to_qdrant.py --spam-learning
  python scripts/sync_rag_to_qdrant.py --contractors --from-db
  python scripts/sync_rag_to_qdrant.py --departments --from-routing-rules --replace-departments
  python scripts/sync_rag_to_qdrant.py --keywords --routing-keywords

По умолчанию (--all): spam_learning из JSON, contractors merge из PostgreSQL,
дополнительные keywords из rag_department_keywords.json и routing_corrections.json.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.db.catalog_repository import CatalogRepository  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.routing.corrections import (  # noqa: E402
    load_corrections,
    migrate_routing_corrections_store,
)
from agent_pochta.routing.learning import (  # noqa: E402
    collect_department_learning_keywords,
    enrich_department_in_qdrant,
)
from agent_pochta.rules.spam_learning import (  # noqa: E402
    SPAM_LEARNING_COLLECTION,
    load_spam_learning,
    resync_spam_learning_to_qdrant,
)
from agent_pochta.services.rag_import import (  # noqa: E402
    load_department_keywords,
    merge_department_keywords,
)
from agent_pochta.services.rag_qdrant import (  # noqa: E402
    CONTRACTORS_COLLECTION,
    DEPARTMENTS_COLLECTION,
    append_department_keywords,
    upsert_contractors_merge,
    upsert_departments_only,
)
from agent_pochta.services.routing_departments import (  # noqa: E402
    build_departments_from_rules,
    load_routing_rules,
)
from agent_pochta.services.spam_learning_rag_qdrant import ensure_spam_learning_indexes  # noqa: E402

_WAIT_SEC = 60
_POLL_SEC = 2


def _wait_for_qdrant(url: str) -> None:
    import httpx

    deadline = time.monotonic() + _WAIT_SEC
    target = f"{url.rstrip('/')}/readyz"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(target, timeout=3.0)
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(_POLL_SEC)
    raise SystemExit(f"Qdrant not ready after {_WAIT_SEC}s: {target}")


def collection_points(url: str, name: str) -> int:
    from qdrant_client import QdrantClient

    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        existing = {c.name for c in client.get_collections().collections}
        if name not in existing:
            return 0
        return client.get_collection(name).points_count or 0
    finally:
        client.close()


def print_collection_stats(url: str, *, prefix: str = "") -> dict[str, int]:
    stats = {
        CONTRACTORS_COLLECTION: collection_points(url, CONTRACTORS_COLLECTION),
        DEPARTMENTS_COLLECTION: collection_points(url, DEPARTMENTS_COLLECTION),
        SPAM_LEARNING_COLLECTION: collection_points(url, SPAM_LEARNING_COLLECTION),
    }
    line = ", ".join(f"{name}={count}" for name, count in stats.items())
    print(f"{prefix}{line}")
    return stats


def sync_spam_learning_from_json() -> dict:
    settings = get_settings()
    if settings.rag_backend != "qdrant":
        return {"synced": 0, "reason": "stub_backend"}
    ensure_spam_learning_indexes(settings.qdrant_url)
    json_entries = len(load_spam_learning().get("entries") or [])
    result = resync_spam_learning_to_qdrant()
    result["json_entries"] = json_entries
    result["qdrant_points"] = collection_points(settings.qdrant_url, SPAM_LEARNING_COLLECTION)
    return result


def sync_contractors_from_db() -> dict:
    settings = get_settings()
    if settings.rag_backend != "qdrant":
        return {"upserted": 0, "reason": "stub_backend"}

    factory = get_session_factory()
    with factory() as session:
        contractors = CatalogRepository(session).load_active_contractors()

    if not contractors:
        return {"upserted": 0, "reason": "no_contractors_in_db"}

    upserted = upsert_contractors_merge(settings.qdrant_url, contractors)
    return {
        "upserted": upserted,
        "contractors_in_db": len(contractors),
        "qdrant_points": collection_points(settings.qdrant_url, CONTRACTORS_COLLECTION),
    }


def sync_departments_from_routing_rules(*, replace: bool = False) -> dict:
    settings = get_settings()
    if settings.rag_backend != "qdrant":
        return {"upserted": 0, "reason": "stub_backend"}

    departments = build_departments_from_rules(load_routing_rules())
    extra = load_department_keywords()
    departments = merge_department_keywords(departments, extra)
    points = upsert_departments_only(settings.qdrant_url, departments, replace=replace)
    return {
        "upserted": points,
        "departments_in_source": len(departments),
        "qdrant_points": collection_points(settings.qdrant_url, DEPARTMENTS_COLLECTION),
        "replace": replace,
    }


def apply_rag_department_keywords() -> dict:
    settings = get_settings()
    if settings.rag_backend != "qdrant":
        return {"updated": 0, "reason": "stub_backend"}

    extra = load_department_keywords()
    updated = 0
    keywords_added = 0
    for department_id, keywords in extra.items():
        result = append_department_keywords(settings.qdrant_url, department_id, keywords)
        if result.get("updated"):
            updated += 1
            keywords_added += int(result.get("keywords_added") or 0)
    return {
        "departments_touched": len(extra),
        "departments_updated": updated,
        "keywords_added": keywords_added,
    }


def apply_routing_correction_keywords() -> dict:
    settings = get_settings()
    if settings.rag_backend != "qdrant":
        return {"updated": 0, "reason": "stub_backend"}

    entries = load_corrections().get("entries") or []
    updated = 0
    keywords_added = 0
    for entry in entries:
        department_id = entry.get("department_id")
        if not department_id:
            continue
        keywords = collect_department_learning_keywords(entry)
        result = enrich_department_in_qdrant(department_id, keywords)
        if result.get("updated"):
            updated += 1
            keywords_added += int(result.get("keywords_added") or 0)
    return {
        "corrections": len(entries),
        "departments_updated": updated,
        "keywords_added": keywords_added,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync local RAG/learning data to Qdrant")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Синхронизировать всё (по умолчанию, если не указаны отдельные флаги)",
    )
    parser.add_argument("--spam-learning", action="store_true", help="JSON spam_learning → Qdrant")
    parser.add_argument(
        "--contractors",
        action="store_true",
        help="Контрагенты merge из PostgreSQL (erp_contractors)",
    )
    parser.add_argument(
        "--departments",
        action="store_true",
        help="Отделы из routing_rules.json + rag_department_keywords.json",
    )
    parser.add_argument(
        "--replace-departments",
        action="store_true",
        help="С --departments: полная перезапись коллекции departments",
    )
    parser.add_argument(
        "--keywords",
        action="store_true",
        help="Добавить keywords из rag_department_keywords.json к существующим отделам",
    )
    parser.add_argument(
        "--routing-keywords",
        action="store_true",
        help="Добавить keywords из routing_corrections.json к отделам",
    )
    parser.add_argument(
        "--migrate-routing-corrections",
        action="store_true",
        help="Пересчитать keywords в routing_corrections.json и удалить message_id",
    )
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Алиас для --contractors (совместимость)",
    )
    parser.add_argument(
        "--from-routing-rules",
        action="store_true",
        help="Алиас для --departments (совместимость)",
    )
    args = parser.parse_args()

    selected = any(
        (
            args.spam_learning,
            args.contractors,
            args.departments,
            args.keywords,
            args.routing_keywords,
            args.migrate_routing_corrections,
            args.from_db,
            args.from_routing_rules,
        )
    )
    run_all = args.all or not selected

    if args.migrate_routing_corrections:
        result = migrate_routing_corrections_store()
        print(f"[sync_rag_to_qdrant] migrate routing_corrections: {result}")

    settings = get_settings()
    url = settings.qdrant_url
    print(f"[sync_rag_to_qdrant] RAG_BACKEND={settings.rag_backend}, url={url}")
    qdrant_selected = run_all or any(
        (
            args.spam_learning,
            args.contractors,
            args.departments,
            args.keywords,
            args.routing_keywords,
            args.from_db,
            args.from_routing_rules,
        )
    )
    if not qdrant_selected:
        return
    if settings.rag_backend != "qdrant":
        print("RAG_BACKEND != qdrant — синхронизация пропущена.")
        return

    print(f"[sync_rag_to_qdrant] waiting for {url} …")
    _wait_for_qdrant(url)

    print("[sync_rag_to_qdrant] before:")
    print_collection_stats(url, prefix="  ")

    if run_all or args.spam_learning:
        result = sync_spam_learning_from_json()
        print(
            f"[sync_rag_to_qdrant] spam_learning: synced={result.get('synced')}/"
            f"{result.get('total')} (json={result.get('json_entries')}, "
            f"qdrant={result.get('qdrant_points')}, pruned={result.get('pruned', 0)})"
        )

    if run_all or args.contractors or args.from_db:
        result = sync_contractors_from_db()
        print(f"[sync_rag_to_qdrant] contractors: {result}")

    if args.departments or args.from_routing_rules:
        result = sync_departments_from_routing_rules(replace=args.replace_departments)
        print(f"[sync_rag_to_qdrant] departments: {result}")

    if run_all or args.keywords:
        result = apply_rag_department_keywords()
        print(f"[sync_rag_to_qdrant] rag_department_keywords: {result}")

    if run_all or args.routing_keywords:
        result = apply_routing_correction_keywords()
        print(f"[sync_rag_to_qdrant] routing_corrections keywords: {result}")

    print("[sync_rag_to_qdrant] after:")
    print_collection_stats(url, prefix="  ")


if __name__ == "__main__":
    main()
