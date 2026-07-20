"""Синхронизация RAG-коллекций из 1С OData или JSON (раздел 9 ТЗ).

Примеры:
  python scripts/sync_rag_from_1c.py --json data/rag_catalog.example.json --source json
  python scripts/sync_rag_from_1c.py --odata --source 1c
  python scripts/sync_rag_from_1c.py --json data/rag_catalog.example.json --skip-qdrant
  python scripts/sync_rag_from_1c.py --json data/rag_catalog.example.json --dry-run
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
from agent_pochta.db.catalog_repository import persist_catalog_to_db  # noqa: E402
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.rag_import import (  # noqa: E402
    load_catalog_from_json,
    load_department_keywords,
    merge_department_keywords,
    odata_rows_to_contractors,
    odata_rows_to_departments,
)
from agent_pochta.services.rag_qdrant import upsert_rag_catalog  # noqa: E402


def _load_from_odata(settings) -> tuple[list, list, list, list]:
    if not settings.odata_base_url:
        raise SystemExit("Задайте ODATA_BASE_URL в .env")
    if not settings.odata_contractors_entity or not settings.odata_departments_entity:
        raise SystemExit("Задайте ODATA_CONTRACTORS_ENTITY и ODATA_DEPARTMENTS_ENTITY в .env")

    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
    )
    contractor_rows = client.fetch_all(settings.odata_contractors_entity)
    department_rows = client.fetch_all(settings.odata_departments_entity)
    return (
        odata_rows_to_contractors(contractor_rows),
        odata_rows_to_departments(department_rows),
        contractor_rows,
        department_rows,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync RAG contractors/departments to Qdrant")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--json", metavar="PATH", help="JSON-каталог (выгрузка / пример)")
    source.add_argument("--odata", action="store_true", help="Читать из 1С OData (.env)")
    parser.add_argument("--dry-run", action="store_true", help="Только показать, без записи")
    parser.add_argument("--skip-db", action="store_true", help="Не писать в PostgreSQL")
    parser.add_argument("--skip-qdrant", action="store_true", help="Не писать в Qdrant")
    parser.add_argument("--source", default="1c", help="Метка источника (1c, json, platform, …)")
    parser.add_argument(
        "--keywords",
        metavar="PATH",
        help="JSON с keywords по department_id (по умолчанию data/rag_department_keywords.json)",
    )
    args = parser.parse_args()

    settings = get_settings()
    keywords_path = Path(args.keywords) if args.keywords else None

    contractor_raw: list[dict] | None = None
    department_raw: list[dict] | None = None
    catalog_source = args.source

    if args.json:
        contractors, departments = load_catalog_from_json(Path(args.json))
        source_label = f"JSON {args.json}"
        catalog_source = args.source if args.source != "1c" else "json"
    else:
        contractors, departments, contractor_raw, department_raw = _load_from_odata(settings)
        source_label = f"OData {settings.odata_base_url}"

    extra_keywords = load_department_keywords(keywords_path)
    departments = merge_department_keywords(departments, extra_keywords)

    print(f"Источник: {source_label} (source={catalog_source})")
    print(f"  contractors: {len(contractors)} записей")
    print(f"  departments: {len(departments)} записей")
    for dept in departments:
        print(f"    • {dept.department_id}: {dept.department_name} ({len(dept.keywords)} keywords)")

    if args.dry_run:
        print("\n--dry-run: запись пропущена.")
        return

    if not args.skip_db:
        run_id = persist_catalog_to_db(
            contractors,
            departments,
            source=catalog_source,
            notes=source_label,
            contractor_raw=contractor_raw,
            department_raw=department_raw,
        )
        print(f"\nPostgreSQL: catalog_sync_runs.id = {run_id}")
        print(f"  erp_contractors / erp_departments обновлены (source={catalog_source})")

    if args.skip_qdrant:
        return

    points_c, points_d = upsert_rag_catalog(
        settings.qdrant_url,
        contractors,
        departments,
        replace=True,
    )
    print(f"\nQdrant ({settings.qdrant_url}):")
    print(f"  contractors: {points_c} точек (по email)")
    print(f"  departments: {points_d} точек")


if __name__ == "__main__":
    main()
