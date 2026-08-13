"""Синхронизация актуального плана производства из 1С OData в PostgreSQL."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.onec_production_plan import (
    OnecProductionPlanHeader,
    OnecProductionPlanItem,
    OnecProductionPlanSyncRun,
)
from app.services.onec_production_plan_matrix import build_production_plan_matrices
from app.services.onec_production_plan_probe import fetch_latest_production_plan_from_onec


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.startswith("0001-01-01"):
        return None
    if "." in text and len(text) >= 10:
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
            try:
                parsed = datetime.strptime(text, fmt)
                return parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _month_key(*values: object) -> str:
    for value in values:
        parsed = _parse_datetime(value)
        if parsed is not None:
            return f"{parsed.year:04d}-{parsed.month:02d}"
    return ""


def _to_float(value: object) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return 0.0


def _to_int(value: object) -> int:
    try:
        return int(float(str(value or "0").replace(",", ".")))
    except ValueError:
        return 0


def _nomenclature_key(item: dict[str, Any]) -> str:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    for key in ("Номенклатура_Key", "Продукция_Key", "Изделие_Key", "Материал_Key"):
        value = raw.get(key)
        if value:
            return str(value)
    return ""


async def ensure_onec_production_plan_tables(db: AsyncSession | None = None) -> None:
    from app.services.onec_db_schema import ensure_onec_agent_tables

    await ensure_onec_agent_tables()


async def replace_production_plan_in_db(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """Заменяет текущий срез плана производства в БД на последний документ из 1С."""
    await ensure_onec_production_plan_tables(db)
    started = datetime.now(timezone.utc)
    header = payload.get("header") or {}
    items = payload.get("items") or []
    plan_ref_key = str(header.get("ref_key") or "")
    plan_number = str(header.get("number") or "")
    plan_date = _parse_datetime(header.get("date"))

    run = OnecProductionPlanSyncRun(
        id=uuid.uuid4(),
        source=str(payload.get("source") or "Document_ПланПроизводства"),
        status="running",
        plan_ref_key=plan_ref_key,
        plan_number=plan_number,
        plan_date=plan_date,
        fetched_count=len(items),
        saved_count=0,
        started_at=started,
    )
    db.add(run)
    await db.flush()

    await db.execute(delete(OnecProductionPlanItem))
    await db.execute(delete(OnecProductionPlanHeader))
    now = datetime.now(timezone.utc)
    db.add(
        OnecProductionPlanHeader(
            id=uuid.uuid4(),
            ref_key=plan_ref_key,
            number=plan_number,
            plan_date=plan_date,
            posted=bool(header.get("posted")),
            deletion_mark=bool(header.get("deletion_mark")),
            source_entity=str(payload.get("source") or ""),
            raw_json=json.dumps(header, ensure_ascii=False),
            synced_at=now,
        )
    )

    saved_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        product_date = _parse_datetime(item.get("date"))
        db.add(
            OnecProductionPlanItem(
                id=uuid.uuid4(),
                plan_ref_key=plan_ref_key,
                line_number=_to_int(item.get("line")),
                product_date=product_date,
                month_key=_month_key(item.get("date"), header.get("date")),
                nomenclature_key=_nomenclature_key(item),
                nomenclature_code=str(item.get("code") or ""),
                nomenclature_name=str(item.get("name") or "").strip(),
                qty=_to_float(item.get("quantity")),
                unit=str(item.get("unit") or ""),
                department=str(item.get("department") or ""),
                raw_json=json.dumps(raw, ensure_ascii=False),
            )
        )
        saved_count += 1

    run.status = "ok"
    run.saved_count = saved_count
    run.finished_at = datetime.now(timezone.utc)
    return {
        "saved_count": saved_count,
        "db_count": saved_count,
        "sync_run_id": str(run.id),
        "plan_ref_key": plan_ref_key,
        "plan_number": plan_number,
        "plan_date": plan_date.isoformat() if plan_date else None,
    }


async def sync_onec_production_plan_to_db(db: AsyncSession) -> dict[str, Any]:
    payload = fetch_latest_production_plan_from_onec()
    if not payload.get("ok"):
        return {
            "step": "production_plan",
            "ok": False,
            "message": payload.get("message"),
            "count": payload.get("count") or 0,
        }
    saved = await replace_production_plan_in_db(db, payload)
    db_match = saved["db_count"] == payload["count"]
    return {
        "step": "production_plan",
        "ok": True,
        "message": payload.get("message"),
        "count": payload.get("count") or 0,
        "saved_count": saved["saved_count"],
        "db_count": saved["db_count"],
        "sync_run_id": saved["sync_run_id"],
        "db_match": db_match,
        "plan_ref_key": saved["plan_ref_key"],
        "plan_number": saved["plan_number"],
        "plan_date": saved["plan_date"],
    }


async def get_production_plan_sync_status(db: AsyncSession, *, ensure: bool = True) -> dict[str, Any]:
    if ensure:
        await ensure_onec_production_plan_tables(db)
    run = (
        await db.execute(
            select(OnecProductionPlanSyncRun).order_by(OnecProductionPlanSyncRun.started_at.desc())
        )
    ).scalars().first()
    db_count = int(
        (
            await db.execute(select(func.count()).select_from(OnecProductionPlanItem))
        ).scalar_one()
        or 0
    )
    if run is None:
        return {
            "last_sync_at": None,
            "status": None,
            "saved_count": 0,
            "db_count": db_count,
            "plan_number": "",
            "plan_date": None,
            "error_message": None,
        }
    return {
        "last_sync_at": (run.finished_at or run.started_at).isoformat()
        if (run.finished_at or run.started_at)
        else None,
        "status": run.status,
        "saved_count": run.saved_count,
        "db_count": db_count,
        "plan_number": run.plan_number,
        "plan_date": run.plan_date.isoformat() if run.plan_date else None,
        "error_message": run.error_message,
    }


async def list_latest_production_plan_from_db(db: AsyncSession) -> dict[str, Any]:
    await ensure_onec_production_plan_tables(db)
    header = (
        await db.execute(
            select(OnecProductionPlanHeader).order_by(OnecProductionPlanHeader.plan_date.desc())
        )
    ).scalars().first()
    rows = (
        await db.execute(
            select(OnecProductionPlanItem).order_by(
                OnecProductionPlanItem.month_key,
                OnecProductionPlanItem.line_number,
                OnecProductionPlanItem.nomenclature_name,
            )
        )
    ).scalars().all()
    values = [
        ["Строка", "Месяц", "Код", "Номенклатура", "Количество", "Ед.", "Подразделение"],
        *[
            [
                str(row.line_number),
                row.month_key,
                row.nomenclature_code,
                row.nomenclature_name,
                f"{row.qty:g}",
                row.unit,
                row.department,
            ]
            for row in rows
        ],
    ]
    matrix_view = build_production_plan_matrices(rows) if header else {
        "month_keys": [],
        "default_month": "",
        "matrices": {},
    }
    return {
        "ok": header is not None,
        "message": (
            f"План производства из БД №{header.number} от "
            f"{header.plan_date.strftime('%d.%m.%Y %H:%M:%S') if header.plan_date else '—'}"
            if header
            else "План производства ещё не синхронизирован в БД."
        ),
        "source": header.source_entity if header else "onec_production_plan_items",
        "count": len(rows),
        "header": {
            "ref_key": header.ref_key,
            "number": header.number,
            "date": header.plan_date.isoformat() if header.plan_date else "",
            "posted": header.posted,
            "deletion_mark": header.deletion_mark,
        }
        if header
        else None,
        "values": values if header else [],
        "matrix_view": matrix_view,
        "table_entities": [header.source_entity] if header else [],
    }
