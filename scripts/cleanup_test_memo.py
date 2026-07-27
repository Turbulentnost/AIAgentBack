"""Удаляет тестовую СЗ TEST-000001 из реестра PostgreSQL и Redis-кэша."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, timedelta

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.services.meeting_memo_cache import (
    _cache_key,
    _dashboard_cache_key,
    find_dashboard_item,
)
from app.services.meeting_redis import get_meeting_redis

DEFAULT_REF_KEY = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
DEFAULT_NUMBER = "TEST-000001"


def delete_registry_row(ref_key: str) -> int:
    engine = create_engine(settings.DATABASE_URL_SYNC)
    normalized = ref_key.strip().lower()
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "DELETE FROM meeting_registry_entries "
                "WHERE memo_ref_key = :ref_key"
            ),
            {"ref_key": normalized},
        )
        conn.commit()
        return int(result.rowcount or 0)


def _remove_item_from_dashboard(payload: dict, ref_key: str, *, day: date) -> dict | None:
    normalized = ref_key.strip().lower()
    if find_dashboard_item(payload, normalized) is None:
        return None

    def strip_list(items: list) -> list:
        return [
            item
            for item in items
            if (item.get("ref_key") or "").strip().lower() != normalized
        ]

    updated = dict(payload)
    raw_date = updated.get("date")
    if isinstance(raw_date, date):
        updated["date"] = raw_date.isoformat()
    else:
        updated["date"] = str(raw_date or day.isoformat())
    for key in ("unapproved", "today", "items"):
        if isinstance(updated.get(key), list):
            updated[key] = strip_list(updated[key])

    counts = dict(updated.get("counts") or {})
    counts["unapproved"] = len(updated.get("unapproved") or [])
    counts["today"] = len(updated.get("today") or [])
    counts["items"] = len(updated.get("items") or [])
    updated["counts"] = counts
    return updated


async def cleanup_redis(ref_key: str, *, days_back: int, days_forward: int) -> dict:
    client = get_meeting_redis()
    normalized = ref_key.strip().lower()
    report: dict[str, object] = {
        "ref_key": normalized,
        "memo_key": _cache_key(normalized),
        "memo_deleted": False,
        "dashboard_updates": [],
    }

    memo_deleted = await client.delete(_cache_key(normalized))
    report["memo_deleted"] = bool(memo_deleted)

    today = date.today()
    for offset in range(-days_back, days_forward + 1):
        day = today + timedelta(days=offset)
        key = _dashboard_cache_key(day)
        raw = await client.get(key)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if find_dashboard_item(payload, normalized) is None:
            continue

        updated = _remove_item_from_dashboard(payload, normalized, day=day)
        if updated is None:
            continue

        ttl = await client.ttl(key)
        if ttl is None or ttl < 0:
            ttl = settings.MEETING_DASHBOARD_CACHE_TTL_SECONDS
        await client.setex(key, ttl, json.dumps(updated, ensure_ascii=False, default=str))
        report["dashboard_updates"].append(day.isoformat())

    return report


async def run(ref_key: str, *, days_back: int, days_forward: int) -> dict:
    deleted_rows = delete_registry_row(ref_key)
    redis_report = await cleanup_redis(
        ref_key,
        days_back=days_back,
        days_forward=days_forward,
    )
    return {
        "ref_key": ref_key.strip().lower(),
        "number": DEFAULT_NUMBER,
        "registry_rows_deleted": deleted_rows,
        **redis_report,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Удалить тестовую СЗ из реестра и Redis.")
    parser.add_argument("--ref-key", default=DEFAULT_REF_KEY)
    parser.add_argument("--days-back", type=int, default=3)
    parser.add_argument("--days-forward", type=int, default=7)
    args = parser.parse_args()

    report = asyncio.run(
        run(
            args.ref_key,
            days_back=args.days_back,
            days_forward=args.days_forward,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
