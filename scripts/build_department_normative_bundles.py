from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.db.session import AsyncSessionLocal
from app.services.department_normative_bundle_service import DepartmentNormativeBundleService


async def run(*, persist_cards: bool, folder_marker: str, output: Path | None) -> None:
    async with AsyncSessionLocal() as session:
        report = await DepartmentNormativeBundleService(session).build_report(
            persist_cards=persist_cards,
            folder_marker=folder_marker,
        )
        if persist_cards:
            await session.commit()

        payload = report.model_dump(mode="json")
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
            print(f"Отчёт сохранён: {output}")
        else:
            print(text)

        print(
            f"\nИтого: подразделений 1С={report.departments_from_1c}, "
            f"документов={report.documents_scanned}, в комплектах={report.documents_assigned}, "
            f"исключено={report.documents_excluded}, вне дерева={report.documents_outside_tree}, "
            f"комплектов={len(report.bundles)}"
        )
        if persist_cards:
            print(
                f"Карточки: создано={report.cards_created}, обновлено={report.cards_updated}, "
                f"пропущено={report.cards_skipped}, таблица={'да' if report.cards_table_available else 'нет'}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Формирует комплекты нормативных документов по подразделениям 1С и заполняет DocumentCard"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только отчёт, без записи карточек в БД",
    )
    parser.add_argument(
        "--folder-marker",
        default="НОРМАТИВНЫЕ ДОКУМЕНТЫ ОРГАНИЗАЦИИ",
        help="Маркер корневой папки импорта в metadata документов",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/department_normative_bundles.json"),
        help="Путь к JSON-отчёту (пустая строка — только stdout)",
    )
    args = parser.parse_args()
    output = None if str(args.output) == "" else args.output
    asyncio.run(
        run(
            persist_cards=not args.dry_run,
            folder_marker=args.folder_marker,
            output=output,
        )
    )


if __name__ == "__main__":
    main()
