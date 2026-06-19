"""Получение всех подразделений из 1С.

Источник данных: Catalog_СтруктураПредприятия через OData.
Используется агентом nd_control_agent и CLI.

Запуск из корня проекта (системный Python, нужны pydantic и requests):
  python -m app.services.onec_departments_fetcher
  python -m app.services.onec_departments_fetcher --json --output departments.json
  python -m app.services.onec_departments_fetcher --query "ОТК" --limit 50

Переменные окружения:
  ONEC_BASE_URL, ODATA_USER, ODATA_PASSWORD
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests
from pydantic import BaseModel, Field

from app.integrations.onec_odata import create_session
from app.services import list_enterprise_positions as onec


class EnterpriseDepartment(BaseModel):
    external_id: str = Field(..., description="Ref_Key подразделения в 1С")
    parent_external_id: str | None = Field(default=None, description="Parent_Key или null для корня")
    name: str = Field(..., description="Краткое наименование подразделения")
    path: str = Field(..., description="Полный путь в иерархии, например «Головной офис / ОТК»")


def fetch_all_departments_from_1c(session: requests.Session | None = None) -> list[EnterpriseDepartment]:
    """Загружает все активные подразделения из Catalog_СтруктураПредприятия."""
    active_session = session or create_session()
    rows = onec.build_enterprise_departments(active_session)
    return [EnterpriseDepartment.model_validate(row) for row in rows]


def filter_departments(
    departments: list[EnterpriseDepartment],
    *,
    query: str | None = None,
    limit: int | None = None,
) -> list[EnterpriseDepartment]:
    if not query:
        filtered = departments
    else:
        needle = onec.normalize_text(query)
        filtered = [
            item
            for item in departments
            if needle in onec.normalize_text(item.name) or needle in onec.normalize_text(item.path)
        ]
    if limit is not None and limit > 0:
        return filtered[:limit]
    return filtered


def format_departments_text(departments: list[EnterpriseDepartment]) -> str:
    if not departments:
        return "Активные подразделения в 1С не найдены.\n"
    lines = [f"Найдено подразделений: {len(departments)}", ""]
    for item in departments:
        lines.append(f"{item.path} [{item.external_id}]")
    return "\n".join(lines) + "\n"


def departments_to_json(departments: list[EnterpriseDepartment]) -> str:
    payload = [item.model_dump() for item in departments]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def save_departments_report(
    departments: list[EnterpriseDepartment],
    output_path: Path | None = None,
    *,
    as_json: bool = False,
) -> Path:
    target = output_path or Path(__file__).resolve().parent / "enterprise_departments_report.txt"
    content = departments_to_json(departments) if as_json else format_departments_text(departments)
    target.write_text(content, encoding="utf-8-sig")
    return target


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Выгрузка всех подразделений из 1С (Catalog_СтруктураПредприятия)")
    parser.add_argument("--query", help="Фильтр по названию или пути подразделения")
    parser.add_argument("--limit", type=int, default=0, help="Ограничить количество строк в выводе")
    parser.add_argument("--output", type=Path, help="Путь к файлу отчёта")
    parser.add_argument("--json", action="store_true", help="Сохранить результат в JSON")
    args = parser.parse_args(argv)

    try:
        departments = fetch_all_departments_from_1c()
        departments = filter_departments(
            departments,
            query=args.query,
            limit=args.limit or None,
        )
        if args.output or args.json:
            path = save_departments_report(departments, args.output, as_json=args.json)
            print(f"Отчёт сохранён: {path}")
        else:
            print(format_departments_text(departments), end="")
        print(f"Всего подразделений: {len(departments)}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
