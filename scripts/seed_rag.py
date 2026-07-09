"""Начальное наполнение RAG-коллекций Qdrant (раздел 9 ТЗ).

Запуск:
  python scripts/seed_rag.py           # только вывод демо-данных
  python scripts/seed_rag.py --load    # загрузка в Qdrant (docker compose up -d)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.rag import _DEMO_CONTRACTORS, _DEMO_DEPARTMENTS  # noqa: E402
from agent_pochta.services.rag_qdrant import seed_qdrant, upsert_rag_catalog  # noqa: E402
from agent_pochta.services.routing_departments import (  # noqa: E402
    build_departments_from_rules,
    load_routing_rules,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed RAG collections for agent-pochta")
    parser.add_argument(
        "--load",
        action="store_true",
        help="Загрузить данные в Qdrant (QDRANT_URL из .env)",
    )
    parser.add_argument(
        "--from-routing-rules",
        action="store_true",
        help="Departments из routing_rules.json (contractors — демо, если не указано иначе)",
    )
    args = parser.parse_args()

    if args.from_routing_rules:
        departments = build_departments_from_rules(load_routing_rules())
        contractors = _DEMO_CONTRACTORS
    else:
        contractors = _DEMO_CONTRACTORS
        departments = _DEMO_DEPARTMENTS

    print("Коллекция contractors:")
    for contractor in contractors:
        print(
            f"  • {contractor.contractor_id}: {contractor.name} "
            f"{contractor.emails} → отделы {contractor.department_codes}"
        )
    print("\nКоллекция departments:")
    for department in departments:
        print(
            f"  • {department.department_id}: {department.department_name} "
            f"(рук. {department.head_name}) — {len(department.keywords)} keywords"
        )

    if not args.load:
        print("\nДобавьте --load для записи в Qdrant.")
        return

    settings = get_settings()
    if args.from_routing_rules:
        points_c, points_d = upsert_rag_catalog(
            settings.qdrant_url,
            contractors,
            departments,
            replace=True,
        )
        print(f"\nЗагружено в Qdrant ({settings.qdrant_url}):")
        print(f"  contractors: {points_c} точек (демо)")
        print(f"  departments: {points_d} точек (routing_rules)")
    else:
        points_c, points_d = seed_qdrant(settings.qdrant_url)
        print(f"\nЗагружено в Qdrant ({settings.qdrant_url}):")
        print(f"  contractors: {points_c} точек")
        print(f"  departments: {points_d} точек")


if __name__ == "__main__":
    main()
