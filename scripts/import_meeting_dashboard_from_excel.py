"""Импорт очереди СЗ из Excel-выгрузки 1С в Redis-кэш dashboard и memo."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.agents.meeting_agent.dashboard import (
    UNAPPROVED_STATUS,
    is_memo_document_date_on_date,
    merge_dashboard_items,
)
from app.agents.meeting_agent.memo_presenter import build_queue_item_from_row
from app.core.config import settings
from app.services.meeting_dashboard_cache import MeetingDashboardCacheService
from app.services.meeting_memo_cache import MeetingMemoCacheService, build_detail_from_dashboard_item
from app.services.meeting_memo_document import parse_odata_datetime
from app.tools.onec.connection import CONFIG, create_session
from app.tools.onec.service_memo_shared import resolve_memo_ref_key

REF_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
HEADER_ROW_INDEX = 2
DATA_START_INDEX = 3
EMPTY_MARKERS = ("<пустая строка>", "<0>", "<пустая ссылка")


def _clean(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in EMPTY_MARKERS):
        return None
    return text


def _to_odata_datetime(value: Any) -> str | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    parsed = parse_odata_datetime(cleaned)
    if parsed is None:
        return cleaned
    return parsed.strftime("%Y-%m-%dT%H:%M:%S")


def _normalize_status(raw: str | None) -> str | None:
    cleaned = _clean(raw)
    if not cleaned:
        return None
    if cleaned.lower() == "не согласована":
        return UNAPPROVED_STATUS
    return cleaned


def _person(full_name: str | None) -> dict[str, Any] | None:
    cleaned = _clean(full_name)
    if not cleaned:
        return None
    return {"full_name": cleaned}


def _read_excel_rows(path: Path) -> pd.DataFrame:
    frame = pd.read_excel(path, header=None)
    headers = [_clean(value) or "" for value in frame.iloc[HEADER_ROW_INDEX].tolist()]
    data = frame.iloc[DATA_START_INDEX:].copy()
    data.columns = headers
    data = data[data["Номер"].notna()].copy()
    return data


def _resolve_ref_key(number: str, *, use_onec: bool) -> tuple[str, str]:
    if use_onec:
        try:
            session = create_session(CONFIG)
            ref_key = resolve_memo_ref_key(session, CONFIG, ref_key=None, number=number)
            return ref_key.strip().lower(), "onec"
        except Exception:
            pass
    ref_key = str(uuid.uuid5(REF_NAMESPACE, f"meeting-memo:{number}"))
    return ref_key, "generated"


def _build_header_from_group(number: str, group: pd.DataFrame, *, ref_key: str) -> dict[str, Any]:
    row = group.iloc[0]
    participants = [
        {"LineNumber": index + 1, "Участник": name}
        for index, name in enumerate(
            dict.fromkeys(
                cleaned
                for cleaned in (_clean(value) for value in group.get("Участник", pd.Series(dtype=object)))
                if cleaned
            )
        )
    ]
    plan_rows = [
        {"LineNumber": index + 1, "Задача": task}
        for index, task in enumerate(
            dict.fromkeys(
                cleaned
                for cleaned in (_clean(value) for value in group.get("Задача", pd.Series(dtype=object)))
                if cleaned
            )
        )
    ]
    return {
        "Ref_Key": ref_key,
        "Number": number,
        "Date": _to_odata_datetime(row.get("Дата")),
        "Статус": _normalize_status(row.get("Статус")),
        "ТемаСлужебнойЗаписки": _clean(row.get("ТемаСлужебнойЗаписки")),
        "ТемаСовещания": _clean(row.get("ТемаСовещания")),
        "ЦельПланаСовещания": _clean(row.get("ЦельПланаСовещания")),
        "ЖелаемаяДатаПроведенияСовещания": _to_odata_datetime(row.get("ЖелаемаяДатаПроведенияСовещания")),
        "ДатаПроведенияСовещания": _to_odata_datetime(row.get("ДатаПроведенияСовещания")),
        "ВремяНачалаСовещания": _to_odata_datetime(row.get("ВремяНачалаСовещания")),
        "ВремяОкончанияСовещания": _to_odata_datetime(row.get("ВремяОкончанияСовещания")),
        "МестоПроведенияСовещания": _clean(row.get("МестоПроведенияСовещания")),
        "ВидСовещания": _clean(row.get("ВидСовещания")),
        "Комментарий": _clean(row.get("Комментарий")),
        "Ответственный": _clean(row.get("Ответственный")),
        "РуководительСовещания": _clean(row.get("РуководительСовещания")),
        "СписокУчастников": participants,
        "ПланСовещания": plan_rows,
    }


def _patch_queue_item(item: dict[str, Any], header: dict[str, Any]) -> dict[str, Any]:
    patched = dict(item)
    initiator = _person(header.get("Ответственный"))
    manager = _person(header.get("РуководительСовещания")) or initiator
    participant_names = [
        row["Участник"]
        for row in header.get("СписокУчастников") or []
        if isinstance(row, dict) and _clean(row.get("Участник"))
    ]
    patched.update(
        {
            "initiator": initiator,
            "manager": manager,
            "participant_names": participant_names,
            "participants_count": max(len(participant_names), patched.get("participants_count") or 0),
            "ПланСовещания": header.get("ПланСовещания") or [],
            "СписокУчастников": header.get("СписокУчастников") or [],
            "ЦельПланаСовещания": header.get("ЦельПланаСовещания"),
            "МестоПроведенияСовещания": header.get("МестоПроведенияСовещания"),
        }
    )
    return patched


def load_memos_from_excel(path: Path, *, use_onec: bool) -> list[dict[str, Any]]:
    data = _read_excel_rows(path)
    memos: list[dict[str, Any]] = []
    for number, group in data.groupby("Номер", sort=False):
        memo_number = _clean(number)
        if not memo_number:
            continue
        ref_key, ref_source = _resolve_ref_key(memo_number, use_onec=use_onec)
        header = _build_header_from_group(memo_number, group, ref_key=ref_key)
        queue_item = _patch_queue_item(build_queue_item_from_row(header), header)
        detail = build_detail_from_dashboard_item(queue_item)
        detail["cache_source"] = "excel"
        detail["history"] = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": "СЗ загружена из Excel (offline cache)",
            }
        ]
        if initiator := queue_item.get("initiator"):
            detail["application"]["initiator"] = initiator
        if manager := queue_item.get("manager"):
            detail["application"]["manager"] = manager
        memos.append(
            {
                "number": memo_number,
                "ref_key": ref_key,
                "ref_source": ref_source,
                "header": header,
                "queue_item": queue_item,
                "detail": detail,
            }
        )
    return memos


def build_dashboard_payload(memos: list[dict[str, Any]], *, dashboard_day: date) -> dict[str, Any]:
    unapproved: list[dict[str, Any]] = []
    today: list[dict[str, Any]] = []
    for memo in memos:
        item = memo["queue_item"]
        header = memo["header"]
        status = header.get("Статус")
        if status == UNAPPROVED_STATUS:
            unapproved.append(item)
        if is_memo_document_date_on_date(header, dashboard_day):
            today.append(item)
    items = merge_dashboard_items(unapproved, today)
    return {
        "date": dashboard_day.isoformat(),
        "unapproved": unapproved,
        "today": today,
        "items": items,
        "counts": {
            "unapproved": len(unapproved),
            "today": len(today),
            "items": len(items),
        },
    }


async def seed_redis(
    memos: list[dict[str, Any]],
    *,
    dashboard_day: date,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc)
    memo_service = MeetingMemoCacheService()
    dashboard_service = MeetingDashboardCacheService()

    for memo in memos:
        await memo_service._write_cache(memo["ref_key"], memo["detail"], fetched_at=fetched_at)

    payload = build_dashboard_payload(memos, dashboard_day=dashboard_day)
    await dashboard_service._write_cache(dashboard_day, payload, fetched_at=fetched_at)
    return {
        "dashboard_date": dashboard_day.isoformat(),
        "memos_loaded": len(memos),
        "counts": payload["counts"],
        "memos": [
            {
                "number": memo["number"],
                "ref_key": memo["ref_key"],
                "ref_source": memo["ref_source"],
                "status": memo["header"].get("Статус"),
                "title": memo["queue_item"].get("title"),
            }
            for memo in memos
        ],
    }


async def run(path: Path, *, dashboard_day: date, use_onec: bool) -> dict[str, Any]:
    memos = load_memos_from_excel(path, use_onec=use_onec)
    if not memos:
        raise RuntimeError(f"В файле {path} не найдено служебных записок")
    redis_report = await seed_redis(memos, dashboard_day=dashboard_day)
    return {
        "source": str(path),
        "redis_url": settings.REDIS_URL,
        **redis_report,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Импорт СЗ из Excel в Redis-кэш агента совещаний.")
    parser.add_argument("excel_path", type=Path, help="Путь к Excel-выгрузке из 1С")
    parser.add_argument(
        "--dashboard-date",
        default="2026-07-09",
        help="Дата dashboard в Redis (ISO, по умолчанию 2026-07-09)",
    )
    parser.add_argument(
        "--use-onec",
        action="store_true",
        help="Попытаться получить Ref_Key из 1С по номеру СЗ",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    report = asyncio.run(
        run(
            args.excel_path,
            dashboard_day=date.fromisoformat(args.dashboard_date),
            use_onec=args.use_onec,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
