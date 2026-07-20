"""Очистка устаревших кодов подразделений в JSON-правилах.

Миграции:
  037, 109, 122 → 163 ТЕХНИЧЕСКИЙ ДИРЕКТОР
  139, 140, 141 → 042 ОРКК
  105, 075 → 155 Отдел дилерских продаж
  034 → 152 ОПЕРАЦИОННЫЙ ДИРЕКТОР (email uprdir)
  131 → 128 Отдел продаж БМИ (exact_email, перед удалением)

Удаление ключей: 016, 034, 045, 081, 131, 037, 109, 122, 139, 140, 141, 105, 075, 032, 120
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]

MIGRATE: dict[str, tuple[str, str]] = {
    "00-000037": ("00-000163", "ТЕХНИЧЕСКИЙ ДИРЕКТОР"),
    "00-000109": ("00-000163", "ТЕХНИЧЕСКИЙ ДИРЕКТОР"),
    "00-000122": ("00-000163", "ТЕХНИЧЕСКИЙ ДИРЕКТОР"),
    "00-000139": ("00-000042", "Отдел по работе с ключевыми клиентами"),
    "00-000140": ("00-000042", "Отдел по работе с ключевыми клиентами"),
    "00-000141": ("00-000042", "Отдел по работе с ключевыми клиентами"),
    "00-000105": ("00-000155", "Отдел дилерских продаж"),
    "00-000075": ("00-000155", "Отдел дилерских продаж"),
    "00-000034": ("00-000152", "ОПЕРАЦИОННЫЙ ДИРЕКТОР"),
    "00-000131": ("00-000128", "Отдел продаж БМИ"),
}

REMOVE_FROM_DEPARTMENT_NAMES = {
    "00-000016",
    "00-000034",
    "00-000037",
    "00-000045",
    "00-000075",
    "00-000081",
    "00-000105",
    "00-000109",
    "00-000122",
    "00-000131",
    "00-000139",
    "00-000140",
    "00-000141",
}

KEYWORD_REDIRECT_BEFORE_DELETE = {
    "00-000016": ("00-000063", "Служба управления персоналом"),
}


def _migrate_code(code: str | None) -> str | None:
    if not code:
        return code
    return MIGRATE.get(str(code).strip(), str(code).strip())


def _migrate_row(row: dict) -> dict:
    code = row.get("code") or row.get("department_id")
    if code and str(code) in MIGRATE:
        new_code, new_name = MIGRATE[str(code)]
        if "code" in row:
            row["code"] = new_code
        if "department_id" in row:
            row["department_id"] = new_code
        if new_name:
            if "name" in row:
                row["name"] = new_name
            if "department_name" in row:
                row["department_name"] = new_name
    return row


def _dedupe(seq: list) -> list:
    seen: set[str] = set()
    out: list = []
    for item in seq:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _merge_keywords(existing: list, extra: list) -> list:
    merged = list(existing or [])
    seen = {str(x).lower() for x in merged}
    for kw in extra or []:
        s = str(kw)
        if s.lower() not in seen:
            merged.append(s)
            seen.add(s.lower())
    return merged


def patch_routing_rules(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    names = data.get("department_names") or {}

    for old, (new, new_name) in KEYWORD_REDIRECT_BEFORE_DELETE.items():
        names.pop(old, None)

    for old in REMOVE_FROM_DEPARTMENT_NAMES:
        names.pop(old, None)

    for _old, (new_code, new_name) in MIGRATE.items():
        if new_code not in names:
            names[new_code] = new_name
    if "00-000128" not in names:
        names["00-000128"] = "Отдел продаж БМИ"
    data["department_names"] = names

    for key in ("email_keyword_rules", "exact_email_rules", "content_rules"):
        rows = data.get(key) or []
        new_rows: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                new_rows.append(row)
                continue
            code = str(row.get("code") or "")
            if code in KEYWORD_REDIRECT_BEFORE_DELETE:
                new_code, new_name = KEYWORD_REDIRECT_BEFORE_DELETE[code]
                row = dict(row)
                row["code"] = new_code
                row["name"] = new_name
                new_rows.append(row)
                continue
            if code in REMOVE_FROM_DEPARTMENT_NAMES and code not in MIGRATE:
                continue
            new_rows.append(_migrate_row(dict(row)))
        data[key] = _dedupe(new_rows)

    for entry in data.get("onec_corrections", {}).get("entries") or []:
        if isinstance(entry, dict):
            _migrate_row(entry)

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_tz_topics(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))

    def merge_into(target: str, source: str) -> None:
        if source not in data:
            return
        src = data.pop(source)
        if target not in data:
            data[target] = {
                "topics": [],
                "names": [],
                "emails": [],
            }
        tgt = data[target]
        tgt["topics"] = _merge_keywords(tgt.get("topics") or [], src.get("topics") or [])
        tgt["names"] = _merge_keywords(tgt.get("names") or [], src.get("names") or [])
        tgt["emails"] = _merge_keywords(tgt.get("emails") or [], src.get("emails") or [])
        if src.get("task_defaults") and not tgt.get("task_defaults"):
            tgt["task_defaults"] = src["task_defaults"]

    for old in ("00-000037", "00-000109", "00-000122"):
        merge_into("00-000163", old)
    for old in ("00-000139", "00-000140", "00-000141"):
        merge_into("00-000042", old)
    for old in ("00-000075", "00-000105"):
        merge_into("00-000155", old)
    if "00-000016" in data:
        merge_into("00-000063", "00-000016")

    for key in (
        "00-000034",
        "00-000032",
        "00-000120",
        "00-000131",
        "00-000045",
        "00-000081",
    ):
        data.pop(key, None)

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_rag_keywords(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))

    def merge_into(target: str, source: str) -> None:
        if source not in data:
            return
        data[target] = _merge_keywords(data.get(target) or [], data.pop(source))

    merge_into("00-000163", "00-000037")
    merge_into("00-000163", "00-000109")
    merge_into("00-000155", "00-000075")
    merge_into("00-000155", "00-000105")
    merge_into("00-000152", "00-000034")
    for key in list(data):
        if key in REMOVE_FROM_DEPARTMENT_NAMES or key in {"00-000032", "00-000120"}:
            data.pop(key, None)
    if "00-000163" not in data:
        data["00-000163"] = []
    if "00-000155" not in data:
        data["00-000155"] = []

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_priority_rules(path: Path) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    replacements = {
        "00-000034": "00-000152",
        "00-000037": "00-000163",
        "00-000075": "00-000155",
        "Исполнительный директор": "ОПЕРАЦИОННЫЙ ДИРЕКТОР",
        "Сервисная служба": "ТЕХНИЧЕСКИЙ ДИРЕКТОР",
        "ОДП) как основной": "Отдел дилерских продаж) как основной",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def patch_enterprise_positions(path: Path) -> None:
    if not path.exists():
        return
    remove_codes = set(REMOVE_FROM_DEPARTMENT_NAMES) | set(MIGRATE) | {"00-000032", "00-000120"}
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("structure_departments_with_codes", "structure_departments_routing_codes"):
        rows = data.get(key) or []
        data[key] = [row for row in rows if str(row.get("code") or "") not in remove_codes]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync_package_routing_rules() -> None:
    src = ROOT / "data/routing_rules.json"
    dst = ROOT / "src/agent_pochta/routing/data/routing_rules.json"
    if dst.exists() and src.exists():
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def patch_routing_corrections(path: Path) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    for old, (new, _name) in MIGRATE.items():
        text = text.replace(f'"department_id": "{old}"', f'"department_id": "{new}"')
        text = text.replace(f'"original_department_id": "{old}"', f'"original_department_id": "{new}"')
    path.write_text(text, encoding="utf-8")


def main() -> int:
    patch_routing_rules(ROOT / "data/routing_rules.json")
    patch_tz_topics(ROOT / "data/tz_department_topics.json")
    patch_rag_keywords(ROOT / "data/rag_department_keywords.json")
    patch_priority_rules(ROOT / "data/document_priority_rules.json")
    patch_enterprise_positions(ROOT / "data/enterprise_positions.json")
    patch_routing_corrections(ROOT / "data/routing_corrections.json")
    sync_package_routing_rules()
    print(
        "Patched routing_rules.json, tz_department_topics.json, rag_department_keywords.json, "
        "document_priority_rules.json, enterprise_positions.json, routing_corrections.json"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
