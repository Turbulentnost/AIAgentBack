"""
Ручной опрос TurboProject: уведомления о новых проектах (без создания серии).

Примеры:
  python -m app.tools.TurboProject.sync_series
  python -m app.tools.TurboProject.sync_series --uploaded-within-days 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.db.session import AsyncSessionLocal
from app.services.turbo_project_series_sync_service import (
    TurboProjectSeriesSyncError,
    TurboProjectSeriesSyncService,
)


async def _run(
    *,
    min_file_id: int | None,
    uploaded_within_days: int | None,
    force_refresh: bool,
) -> dict:
    async with AsyncSessionLocal() as db:
        try:
            result = await TurboProjectSeriesSyncService(db).discover_and_notify(
                min_file_id=min_file_id,
                uploaded_within_days=uploaded_within_days,
                force_refresh=force_refresh,
            )
            await db.commit()
            return result.as_dict()
        except Exception:
            await db.rollback()
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Опрос TurboProject: уведомления о новых проектах для РГ"
    )
    parser.add_argument(
        "--min-file-id",
        type=int,
        default=None,
        help="Опциональный нижний порог file_id (0 = выкл; по умолчанию из настроек)",
    )
    parser.add_argument(
        "--uploaded-within-days",
        type=int,
        default=None,
        help="Только проекты с uploaded_at за N дней (1 = сегодня; по умолчанию из настроек)",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Игнорировать дневной кэш и снова запросить TurboProject",
    )
    args = parser.parse_args(argv)

    try:
        payload = asyncio.run(
            _run(
                min_file_id=args.min_file_id,
                uploaded_within_days=args.uploaded_within_days,
                force_refresh=args.force_refresh,
            )
        )
    except TurboProjectSeriesSyncError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001
        print(str(error), file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("failed", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
