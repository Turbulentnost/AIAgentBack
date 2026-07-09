"""Загрузка справочника отделов (код 1С + название) в PostgreSQL.

Примеры:
  python scripts/seed_departments.py
  python scripts/seed_departments.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.db.department_repository import DepartmentRepository, seed_departments_to_db  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.services.routing_departments import (  # noqa: E402
    build_department_records_for_db,
    load_routing_rules,
    resolve_routing_rules_path,
)


def _print_summary(records) -> None:
    print(f"  departments: {len(records)} записей")
    for record in records[:5]:
        direction = record.direction or "—"
        email = record.email or "—"
        print(f"    • {record.code}: {record.name} [{direction}] {email}")
    if len(records) > 5:
        print(f"    ... ещё {len(records) - 5}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed PostgreSQL departments from JSON sources")
    parser.add_argument(
        "--rules",
        metavar="PATH",
        help="Путь к routing_rules.json (по умолчанию ROUTING_RULES or data/routing_rules.json)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Только показать, без записи в БД")
    args = parser.parse_args()

    rules_path = resolve_routing_rules_path(args.rules)
    rules = load_routing_rules(rules_path)
    records = build_department_records_for_db(rules)

    print(f"Источник: {rules_path}")
    _print_summary(records)

    if args.dry_run:
        print("\n--dry-run: запись в PostgreSQL пропущена.")
        return

    count = seed_departments_to_db(records)
    factory = get_session_factory()
    with factory() as session:
        active = DepartmentRepository(session).count_active()
    print(f"\nЗагружено в PostgreSQL: {count} записей (активных: {active})")


if __name__ == "__main__":
    main()
