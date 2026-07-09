"""Синхронизация коллекции departments в Qdrant из структуры 1С + routing_rules + ТЗ.

Примеры:
  python scripts/sync_departments_from_1c_structure.py
  python scripts/sync_departments_from_1c_structure.py --dry-run
  python scripts/sync_departments_from_1c_structure.py --enterprise data/enterprise_positions.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.rag_qdrant import upsert_departments_only  # noqa: E402
from agent_pochta.services.routing_departments import (  # noqa: E402
    build_departments_from_structure,
    load_routing_rules,
    load_tz_emails_by_code,
    resolve_comparison_report_path,
    resolve_enterprise_positions_path,
    resolve_routing_rules_path,
)


def _print_summary(departments) -> None:
    with_emails = sum(1 for d in departments if any("@" in kw for kw in d.keywords))
    print(f"  departments: {len(departments)} записей ({with_emails} с email в keywords)")
    for dept in departments[:5]:
        email_kws = [kw for kw in dept.keywords if "@" in kw]
        email_hint = f", emails: {', '.join(email_kws[:3])}" if email_kws else ""
        print(f"    • {dept.department_id}: {dept.department_name} ({len(dept.keywords)} kw{email_hint})")
    if len(departments) > 5:
        print(f"    ... ещё {len(departments) - 5}")


def _print_samples_with_emails(departments, limit: int = 5) -> None:
    samples = [d for d in departments if any("@" in kw for kw in d.keywords)][:limit]
    if not samples:
        print("\nОтделы с email в keywords не найдены.")
        return
    print(f"\nПримеры отделов с email ({len(samples)}):")
    for dept in samples:
        emails = sorted({kw for kw in dept.keywords if "@" in kw})
        locals_ = sorted({kw for kw in dept.keywords if "@" not in kw and "." in kw})
        print(f"  {dept.department_id} | {dept.department_name}")
        print(f"    email: {', '.join(emails)}")
        if locals_:
            print(f"    local-part: {', '.join(locals_[:6])}")


def _verify_qdrant(url: str, expected_count: int) -> int:
    from qdrant_client import QdrantClient

    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        info = client.get_collection("departments")
        points_count = info.points_count or 0
        print(f"\nQdrant departments.points_count = {points_count}")
        if points_count != expected_count:
            print(f"  ⚠ ожидалось {expected_count} точек")
        return points_count
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync Qdrant departments from 1C structure + routing_rules + TZ emails"
    )
    parser.add_argument("--rules", metavar="PATH", help="Путь к routing_rules.json")
    parser.add_argument("--enterprise", metavar="PATH", help="Путь к enterprise_positions.json")
    parser.add_argument("--comparison", metavar="PATH", help="Путь к departments_comparison_report.json")
    parser.add_argument("--dry-run", action="store_true", help="Только показать, без записи в Qdrant")
    parser.add_argument("--qdrant-url", metavar="URL", help="URL Qdrant (по умолчанию QDRANT_URL из .env)")
    args = parser.parse_args()

    rules_path = resolve_routing_rules_path(args.rules)
    enterprise_path = resolve_enterprise_positions_path(args.enterprise)
    comparison_path = resolve_comparison_report_path(args.comparison)

    rules = load_routing_rules(rules_path)
    enterprise = json.loads(enterprise_path.read_text(encoding="utf-8"))
    comparison = (
        json.loads(comparison_path.read_text(encoding="utf-8")) if comparison_path else None
    )
    departments = build_departments_from_structure(
        rules,
        enterprise_path=enterprise_path,
        comparison_path=comparison_path,
    )
    tz_emails = load_tz_emails_by_code(enterprise, comparison=comparison)

    print(f"Источник структуры: {enterprise_path}")
    print(f"Источник правил: {rules_path}")
    if comparison_path:
        print(f"Отчёт сравнения: {comparison_path}")
    print(f"Email из ТЗ: {len(tz_emails)} кодов")
    _print_summary(departments)
    _print_samples_with_emails(departments)

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
