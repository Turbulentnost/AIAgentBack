"""Инициализация Qdrant при старте docker compose (rag-init).

- Создаёт коллекции и payload-индексы
- Если departments/contractors пусты — seed из routing_rules + демо-контрагенты
- Если spam_learning пуст — resync из spam_learning_patterns.json

Полная синхронизация вручную:  python scripts/sync_rag_to_qdrant.py
Запуск init:  python scripts/init_qdrant.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.rules.spam_learning import (  # noqa: E402
    SPAM_LEARNING_COLLECTION,
    load_spam_learning,
    resync_spam_learning_to_qdrant,
    resolve_spam_learning_path,
)
from agent_pochta.services.rag import _DEMO_CONTRACTORS  # noqa: E402
from agent_pochta.services.rag_qdrant import (  # noqa: E402
    CONTRACTORS_COLLECTION,
    DEPARTMENTS_COLLECTION,
    QdrantRAGService,
    upsert_rag_catalog,
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


def _collection_points(url: str, name: str) -> int:
    from qdrant_client import QdrantClient

    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        existing = {c.name for c in client.get_collections().collections}
        if name not in existing:
            return 0
        return client.get_collection(name).points_count or 0
    finally:
        client.close()


def main() -> None:
    settings = get_settings()
    url = settings.qdrant_url
    print(f"[init_qdrant] waiting for {url} …")
    _wait_for_qdrant(url)

    # Коллекции + индексы contractors / departments
    QdrantRAGService(url)
    ensure_spam_learning_indexes(url)

    contractors_n = _collection_points(url, CONTRACTORS_COLLECTION)
    departments_n = _collection_points(url, DEPARTMENTS_COLLECTION)
    spam_n = _collection_points(url, SPAM_LEARNING_COLLECTION)

    print(
        f"[init_qdrant] before seed: contractors={contractors_n}, "
        f"departments={departments_n}, spam_learning={spam_n}"
    )

    if departments_n == 0:
        departments = build_departments_from_rules(load_routing_rules())
        contractors = _DEMO_CONTRACTORS if contractors_n == 0 else []
        if contractors:
            points_c, points_d = upsert_rag_catalog(
                url, contractors, departments, replace=contractors_n == 0
            )
        else:
            from agent_pochta.services.rag_qdrant import upsert_departments_only

            points_d = upsert_departments_only(url, departments, replace=True)
            points_c = 0
        print(f"[init_qdrant] seeded departments: contractors={points_c}, departments={points_d}")
        contractors_n = _collection_points(url, CONTRACTORS_COLLECTION)
        departments_n = _collection_points(url, DEPARTMENTS_COLLECTION)
    elif contractors_n == 0:
        _, points_c = upsert_rag_catalog(url, _DEMO_CONTRACTORS, [], replace=False)
        print(f"[init_qdrant] seeded demo contractors: {points_c} points")

    learning_path = resolve_spam_learning_path()
    json_entries = len(load_spam_learning(learning_path).get("entries") or [])
    if spam_n == 0 and json_entries > 0:
        result = resync_spam_learning_to_qdrant(learning_path)
        print(f"[init_qdrant] spam resync: {result}")
        spam_n = _collection_points(url, SPAM_LEARNING_COLLECTION)

    print(
        f"[init_qdrant] done: contractors={contractors_n}, "
        f"departments={departments_n}, spam_learning={spam_n}"
    )


if __name__ == "__main__":
    main()
