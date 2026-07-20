"""Полная синхронизация коллекции departments в Qdrant из routing_rules.json.

Примеры:
  python scripts/sync_departments_from_routing_rules.py
  python scripts/sync_departments_from_routing_rules.py --dry-run
  python scripts/sync_departments_from_routing_rules.py --rules data/routing_rules.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.rag_qdrant import upsert_departments_only  # noqa: E402
from agent_pochta.services.routing_departments import (  # noqa: E402
    build_departments_from_rules,
    load_routing_rules,
    resolve_routing_rules_path,
)


def _print_summary(departments) -> None:
    print(f"  departments: {len(departments)} записей")
    for dept in departments:
        print(f"    • {dept.department_id}: {dept.department_name} ({len(dept.keywords)} keywords)")


def _verify_qdrant(url: str, expected_count: int) -> None:
    from qdrant_client import QdrantClient

    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        info = client.get_collection("departments")
        points_count = info.points_count or 0
        print(f"\nQdrant departments.points_count = {points_count}")
        if points_count != expected_count:
            print(f"  ⚠ ожидалось {expected_count} точек")
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync Qdrant departments collection from routing_rules.json"
    )
    parser.add_argument(
        "--rules",
        metavar="PATH",
        help="Путь к routing_rules.json (по умолчанию ROUTING_RULES or data/routing_rules.json)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Только показать, без записи в Qdrant")
    parser.add_argument(
        "--qdrant-url",
        metavar="URL",
        help="URL Qdrant (по умолчанию QDRANT_URL из .env)",
    )
    args = parser.parse_args()

    rules_path = resolve_routing_rules_path(args.rules)
    rules = load_routing_rules(rules_path)
    departments = build_departments_from_rules(rules)

    print(f"Источник: {rules_path}")
    _print_summary(departments)

    if args.dry_run:
        print("\n--dry-run: запись в Qdrant пропущена.")
        return

    settings = get_settings()
    qdrant_url = args.qdrant_url or settings.qdrant_url
    points = upsert_departments_only(qdrant_url, departments, replace=True)
    print(f"\nЗагружено в Qdrant ({qdrant_url}): {points} точек (departments only)")
    _verify_qdrant(qdrant_url, len(departments))


if __name__ == "__main__":
    main()
