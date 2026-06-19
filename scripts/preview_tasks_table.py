"""Предпросмотр таблицы задач по поручениям и протоколам (tasks_table).

Примеры:
  python scripts/preview_tasks_table.py --author-fio "Амураль Игорь Борисович" --limit 10
  python scripts/preview_tasks_table.py --user-email user@company.ru --limit 20
  python scripts/preview_tasks_table.py --author-fio "Амураль Игорь Борисович" -o tasks_table.json
  python scripts/preview_tasks_table.py --author-fio "Амураль Игорь Борисович" --csv tasks.csv
  python scripts/preview_tasks_table.py --author-fio "Амураль Игорь Борисович" --excel tasks.xlsx
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.agents.tasks_agent.table_presenter import build_tasks_table, write_tasks_table_xlsx
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.services.tasks_manager_resolver import resolve_porucheniya_manager_fio
from app.tools.onec.get_porucheniya import query_porucheniya


async def _resolve_author_fio(user_email: str | None) -> tuple[str | None, str | None]:
    if not user_email:
        return None, None
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == user_email))
        if user is None:
            raise SystemExit(f"Пользователь не найден: {user_email}")
        author_fio, source = await resolve_porucheniya_manager_fio(db, user)
        return author_fio, source


def _print_console_table(table: dict) -> None:
    columns = table.get("columns") or []
    rows = table.get("rows") or []
    titles = [column["title"] for column in columns]
    keys = [column["key"] for column in columns]

    print(f"Строк: {table.get('row_count', len(rows))}")
    print(" | ".join(titles))
    print("-|-".join("-" * len(title) for title in titles))
    for row in rows:
        print(" | ".join(str(row.get(key, "")) for key in keys))


def _write_csv(path: Path, table: dict) -> None:
    columns = table.get("columns") or []
    rows = table.get("rows") or []
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[column["key"] for column in columns],
            extrasaction="ignore",
        )
        writer.writerow({column["key"]: column["title"] for column in columns})
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Предпросмотр tasks_table по поручениям и протоколам из 1С")
    parser.add_argument("--start", help="Начало периода YYYY-MM-DD")
    parser.add_argument("--end", help="Конец периода YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=20, help="Максимум документов на источник")
    parser.add_argument("--author-fio", help="ФИО руководителя для фильтра")
    parser.add_argument("--user-email", help="Email пользователя в БД (роль определит руководителя)")
    parser.add_argument("-o", "--output", help="Сохранить JSON с tasks_table")
    parser.add_argument("--csv", help="Сохранить CSV для Excel")
    parser.add_argument("--excel", help="Сохранить XLSX (Excel)")
    args = parser.parse_args()

    author_fio = (args.author_fio or "").strip() or None
    manager_source = "explicit"
    if author_fio is None and args.user_email:
        author_fio, manager_source = asyncio.run(_resolve_author_fio(args.user_email))
    if author_fio is None:
        parser.error("Укажите --author-fio или --user-email")

    raw = query_porucheniya(
        period_start=args.start,
        period_end=args.end,
        limit=args.limit,
        author_fio=author_fio,
    )
    table = build_tasks_table(
        raw.get("porucheniya") or [],
        raw.get("protocols") or [],
    )
    payload = {
        "author_fio": author_fio,
        "manager_fio_source": manager_source,
        "period_start": raw.get("period_start"),
        "period_end": raw.get("period_end"),
        "counts": raw.get("counts"),
        "tasks_table": table,
    }

    if args.output:
        Path(args.output).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"JSON сохранён: {args.output}")
    if args.csv:
        _write_csv(Path(args.csv), table)
        print(f"CSV сохранён: {args.csv}")
    if args.excel:
        excel_path = Path(args.excel)
        excel_path.parent.mkdir(parents=True, exist_ok=True)
        write_tasks_table_xlsx(excel_path, table)
        print(f"Excel сохранён: {excel_path} ({table.get('row_count', len(table.get('rows') or []))} строк)")
    if not args.output and not args.csv and not args.excel:
        print(
            f"Период: {payload['period_start']} — {payload['period_end']} | "
            f"Руководитель: {author_fio} ({manager_source})"
        )
        print(f"Счётчики: {payload['counts']}")
        _print_console_table(table)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
