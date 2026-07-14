"""Выгрузка код → Ref_Key для OData (организации ТЗ и подразделения 1С).

Читает Catalog_Организации и Catalog_СтруктураПредприятия через OData,
сопоставляет коды ТЗ (НП, АЛ, МГ…) с организациями 1С,
сохраняет JSON для .env / ODATA_*_KEYS_FILE.

Примеры:
  python scripts/export_odata_guid_keys.py
  python scripts/export_odata_guid_keys.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.routing_departments import load_routing_rules  # noqa: E402

DEFAULT_ORG_OUT = ROOT / "data" / "odata_organization_keys.json"
DEFAULT_DEPT_OUT = ROOT / "data" / "odata_department_keys.json"
DEPT_CODE_RE = re.compile(r"^00-\d{6}$")

# Код ТЗ → подстроки в Description организации 1С (см. export_departments_excel.ORG_FULL_NAMES)
TZ_ORG_HINTS: dict[str, list[str]] = {
    "НП": ["турбулентность-дон", "нпо", "сктб"],
    "АЛ": ["алмаз"],
    "МГ": ["метрогаз"],
    "АМ": ["амурская легенда", "акваген"],
    "МИ": ["милака"],
    "БМ": ["бми", "блочно-модульн", "блочно модульн"],
}


def _normalize(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def match_tz_organization(code: str, description: str) -> bool:
    hints = TZ_ORG_HINTS.get(code.upper(), [])
    desc = _normalize(description)
    return any(hint in desc for hint in hints)


def collect_department_codes(rules: dict) -> set[str]:
    codes: set[str] = set()
    reserve = str(rules.get("reserve_code") or "").strip()
    if reserve:
        codes.add(reserve)
    for bucket in (
        "email_keyword_rules",
        "exact_email_rules",
        "content_rules",
        "sender_rules",
        "department_names",
    ):
        items = rules.get(bucket)
        if bucket == "department_names" and isinstance(items, dict):
            codes.update(str(k).strip() for k in items if str(k).strip())
            continue
        if not isinstance(items, list):
            continue
        for rule in items:
            if isinstance(rule, dict):
                code = str(rule.get("code") or "").strip()
                if code:
                    codes.add(code)
    return {code for code in codes if DEPT_CODE_RE.match(code)}


def build_organization_map(rows: list[dict]) -> dict[str, str]:
    result: dict[str, str] = {}
    for tz_code in TZ_ORG_HINTS:
        for row in rows:
            desc = str(row.get("Description") or "").strip()
            ref = str(row.get("Ref_Key") or "").strip()
            if not desc or not ref:
                continue
            if match_tz_organization(tz_code, desc):
                result[tz_code] = ref
                break
    return result


def build_department_map(rows: list[dict], needed_codes: set[str]) -> dict[str, str]:
    by_code: dict[str, str] = {}
    for row in rows:
        code = str(row.get("Code") or "").strip()
        ref = str(row.get("Ref_Key") or "").strip()
        if DEPT_CODE_RE.match(code) and ref:
            by_code[code] = ref
    return {code: by_code[code] for code in sorted(needed_codes) if code in by_code}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export OData organization/department GUID maps")
    parser.add_argument("--org-out", type=Path, default=DEFAULT_ORG_OUT)
    parser.add_argument("--dept-out", type=Path, default=DEFAULT_DEPT_OUT)
    parser.add_argument("--dry-run", action="store_true", help="Только показать, без записи")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.odata_base_url:
        raise SystemExit("Задайте ODATA_BASE_URL в .env")

    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=settings.odata_timeout_sec,
    )

    org_rows = client.fetch_all("Catalog_Организации")
    dept_rows = client.fetch_all("Catalog_СтруктураПредприятия")
    rules = load_routing_rules(settings.routing_rules_path or None)
    needed_dept_codes = collect_department_codes(rules)

    org_map = build_organization_map(org_rows)
    dept_map = build_department_map(dept_rows, needed_dept_codes)

    print("Организации (код ТЗ → Ref_Key):")
    for code, ref in sorted(org_map.items()):
        print(f"  {code}: {ref}")
    missing_orgs = sorted(set(TZ_ORG_HINTS) - set(org_map))
    if missing_orgs:
        print("Не сопоставлены:", ", ".join(missing_orgs))

    print(f"\nПодразделения: {len(dept_map)} из {len(needed_dept_codes)} кодов routing_rules")
    missing_depts = sorted(needed_dept_codes - set(dept_map))
    if missing_depts:
        print("Нет в Catalog_СтруктураПредприятия:", ", ".join(missing_depts[:20]))
        if len(missing_depts) > 20:
            print(f"  … и ещё {len(missing_depts) - 20}")

    if args.dry_run:
        return

    args.org_out.parent.mkdir(parents=True, exist_ok=True)
    args.dept_out.parent.mkdir(parents=True, exist_ok=True)
    args.org_out.write_text(
        json.dumps(org_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.dept_out.write_text(
        json.dumps(dept_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nЗаписано: {args.org_out}")
    print(f"Записано: {args.dept_out}")


if __name__ == "__main__":
    main()
