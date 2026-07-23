"""Импорт файлов из сетевой папки в документы и карточки.

Пример:
  python scripts/import_folder_to_document_cards.py --scan
  python scripts/import_folder_to_document_cards.py --dry-run
  python scripts/import_folder_to_document_cards.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_FOLDER = (
    r"\\192.168.1.198\Files\10.СКТБ\НОРМАТИВНЫЕ ДОКУМЕНТЫ ОРГАНИЗАЦИИ\НОРМАТИВНЫЕ ДОКУМЕНТЫ"
)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Импорт нормативных документов из папки в карточки")
    parser.add_argument("--folder", default=DEFAULT_FOLDER, help="Путь к папке с документами")
    parser.add_argument("--scan", action="store_true", help="Только показать найденные файлы")
    parser.add_argument("--dry-run", action="store_true", help="Проверить импорт без записи в БД")
    parser.add_argument("--no-recursive", action="store_true", help="Не обходить подпапки")
    args = parser.parse_args()

    if args.scan:
        from app.services.document_folder_scan import scan_folder

        files = scan_folder(args.folder, recursive=not args.no_recursive)
        print(json.dumps({"folder_path": args.folder, "total_files": len(files), "files": files}, ensure_ascii=False, indent=2))
        return 0

    from app.db.session import AsyncSessionLocal
    from app.services.document_folder_import_service import DocumentFolderImportService

    async with AsyncSessionLocal() as session:
        service = DocumentFolderImportService(session)
        result = await service.import_folder(
            args.folder,
            recursive=not args.no_recursive,
            dry_run=args.dry_run,
        )
        if args.dry_run:
            await session.rollback()
        else:
            await session.commit()
        print(result.model_dump_json(indent=2))
        return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
