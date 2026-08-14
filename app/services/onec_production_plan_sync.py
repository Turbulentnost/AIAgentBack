"""Синхронизация актуального плана производства из 1С OData в PostgreSQL."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.onec_production_plan import (
    OnecProductionPlanHeader,
    OnecProductionPlanItem,
    OnecProductionPlanSyncRun,
)
from app.services.onec_production_plan_matrix import build_production_plan_matrices
from app.services.onec_production_plan_probe import fetch_production_plans_for_year
from app.services.onec_production_plan_resolver import MonthPlanSource, resolve_year_production_plan


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


def _is_daily_plan_header(header: dict[str, Any]) -> bool:
    text = " ".join(
        str(header.get(key) or "")
        for key in (
            "scenario_name",
            "plan_type_name",
            "scenario_key",
            "plan_type_key",
            "number",
        )
    ).casefold().replace("ё", "е")
    if "день" in text:
        return True
    # В текущей выгрузке 1С дневные планы часто отдают дату только в заголовке.
    # Если строка без даты, используем дату документа как день плана.
    return False


async def ensure_onec_production_plan_tables(db: AsyncSession | None = None) -> None:
    from app.services.onec_db_schema import ensure_onec_agent_tables

    await ensure_onec_agent_tables()


def _document_items(document: dict[str, Any], plan_ref_key: str) -> list[OnecProductionPlanItem]:
    header = document.get("header") or {}
    items = document.get("items") or []
    result: list[OnecProductionPlanItem] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        row_date = _parse_datetime(item.get("date"))
        header_date = _parse_datetime(header.get("date"))
        product_date = row_date or (header_date if _is_daily_plan_header(header) else None)
        result.append(
            OnecProductionPlanItem(
                id=uuid.uuid4(),
                plan_ref_key=plan_ref_key,
                line_number=_to_int(item.get("line")),
                product_date=product_date,
                month_key=_month_key(product_date, item.get("date"), header.get("date")),
                nomenclature_key=_nomenclature_key(item),
                nomenclature_code=str(item.get("code") or ""),
                nomenclature_name=str(item.get("name") or "").strip(),
                specification_key=str(item.get("spec_key") or ""),
                specification_name=str(item.get("spec_name") or "").strip(),
                qty=_to_float(item.get("quantity")),
                unit=str(item.get("unit") or ""),
                department=str(item.get("department") or ""),
                raw_json=json.dumps(raw, ensure_ascii=False),
            )
        )
    return result


async def upsert_production_plans_in_db(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """Сохраняет все документы плана за год и удаляет устаревшие срезы этого года."""
    await ensure_onec_production_plan_tables(db)
    started = datetime.now(timezone.utc)
    year = int(payload.get("year") or date.today().year)
    documents = payload.get("documents") or []
    if not documents and payload.get("header"):
        documents = [
            {
                "header": payload.get("header") or {},
                "items": payload.get("items") or [],
                "source": payload.get("source") or "Document_ПланПроизводства",
            }
        ]

    ref_keys = [str((doc.get("header") or {}).get("ref_key") or "") for doc in documents]
    ref_keys = [ref_key for ref_key in ref_keys if ref_key]
    primary_header = (documents[0].get("header") or {}) if documents else {}
    primary_ref_key = str(primary_header.get("ref_key") or "")
    primary_number = str(primary_header.get("number") or "")
    primary_date = _parse_datetime(primary_header.get("date"))

    run = OnecProductionPlanSyncRun(
        id=uuid.uuid4(),
        source=str(payload.get("source") or "Document_ПланПроизводства"),
        status="running",
        plan_ref_key=primary_ref_key,
        plan_number=primary_number,
        plan_date=primary_date,
        fetched_count=int(payload.get("count") or 0),
        saved_count=0,
        started_at=started,
    )
    db.add(run)
    await db.flush()

    existing_headers = (
        await db.execute(select(OnecProductionPlanHeader))
    ).scalars().all()
    stale_ref_keys = [
        header.ref_key
        for header in existing_headers
        if header.ref_key not in ref_keys
        and (
            (header.period_start and header.period_start.year == year)
            or (header.period_end and header.period_end.year == year)
            or (header.plan_date and header.plan_date.year == year)
        )
    ]
    if stale_ref_keys:
        await db.execute(
            delete(OnecProductionPlanItem).where(
                OnecProductionPlanItem.plan_ref_key.in_(stale_ref_keys)
            )
        )
        await db.execute(
            delete(OnecProductionPlanHeader).where(
                OnecProductionPlanHeader.ref_key.in_(stale_ref_keys)
            )
        )

    saved_count = 0
    now = datetime.now(timezone.utc)
    for document in documents:
        header = document.get("header") or {}
        plan_ref_key = str(header.get("ref_key") or "")
        if not plan_ref_key:
            continue
        plan_number = str(header.get("number") or "")
        plan_date = _parse_datetime(header.get("date"))
        period_start = _parse_datetime(header.get("period_start"))
        period_end = _parse_datetime(header.get("period_end"))
        source_entity = str(document.get("source") or payload.get("source") or "")

        existing = (
            await db.execute(
                select(OnecProductionPlanHeader).where(OnecProductionPlanHeader.ref_key == plan_ref_key)
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                OnecProductionPlanHeader(
                    id=uuid.uuid4(),
                    ref_key=plan_ref_key,
                    number=plan_number,
                    plan_date=plan_date,
                    period_start=period_start,
                    period_end=period_end,
                    posted=bool(header.get("posted")),
                    deletion_mark=bool(header.get("deletion_mark")),
                    source_entity=source_entity,
                    raw_json=json.dumps(header, ensure_ascii=False),
                    synced_at=now,
                )
            )
        else:
            existing.number = plan_number
            existing.plan_date = plan_date
            existing.period_start = period_start
            existing.period_end = period_end
            existing.posted = bool(header.get("posted"))
            existing.deletion_mark = bool(header.get("deletion_mark"))
            existing.source_entity = source_entity
            existing.raw_json = json.dumps(header, ensure_ascii=False)
            existing.synced_at = now

        await db.execute(
            delete(OnecProductionPlanItem).where(OnecProductionPlanItem.plan_ref_key == plan_ref_key)
        )
        for item in _document_items(document, plan_ref_key):
            db.add(item)
            saved_count += 1

    run.status = "ok"
    run.saved_count = saved_count
    run.finished_at = datetime.now(timezone.utc)
    return {
        "saved_count": saved_count,
        "db_count": saved_count,
        "sync_run_id": str(run.id),
        "plan_ref_key": primary_ref_key,
        "plan_number": primary_number,
        "plan_date": primary_date.isoformat() if primary_date else None,
        "documents_count": len(documents),
        "year": year,
    }


async def replace_production_plan_in_db(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """Обратная совместимость."""
    return await upsert_production_plans_in_db(db, payload)


async def sync_onec_production_plan_to_db(db: AsyncSession) -> dict[str, Any]:
    payload = fetch_production_plans_for_year(date.today().year)
    if not payload.get("ok"):
        return {
            "step": "production_plan",
            "ok": False,
            "message": payload.get("message"),
            "count": payload.get("count") or 0,
        }
    saved = await upsert_production_plans_in_db(db, payload)
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
        "documents_count": saved["documents_count"],
        "year": saved["year"],
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


async def _load_all_plan_rows(db: AsyncSession) -> tuple[list[OnecProductionPlanHeader], list[OnecProductionPlanItem]]:
    headers = (
        await db.execute(
            select(OnecProductionPlanHeader).order_by(OnecProductionPlanHeader.plan_date.desc())
        )
    ).scalars().all()
    if not headers:
        return [], []
    rows = (
        await db.execute(
            select(OnecProductionPlanItem).order_by(
                OnecProductionPlanItem.month_key,
                OnecProductionPlanItem.line_number,
                OnecProductionPlanItem.nomenclature_name,
            )
        )
    ).scalars().all()
    return headers, rows


async def list_latest_production_plan_from_db(
    db: AsyncSession,
    *,
    year: int | None = None,
) -> dict[str, Any]:
    await ensure_onec_production_plan_tables(db)
    target_year = year or date.today().year
    headers, rows = await _load_all_plan_rows(db)
    if not headers:
        return {
            "ok": False,
            "year": target_year,
            "message": "План производства ещё не синхронизирован в БД.",
            "source": "onec_production_plan_items",
            "count": 0,
            "header": None,
            "values": [],
            "matrix_view": {
                "month_keys": [],
                "default_month": "",
                "matrices": {},
            },
            "month_sources": {},
            "gaps": [],
            "documents_count": 0,
            "table_entities": [],
        }

    today = date.today()
    current_month = f"{today.year:04d}-{today.month:02d}"
    resolved = resolve_year_production_plan(
        headers,
        rows,
        year=target_year,
        merge_month_keys={current_month},
    )
    primary_header = max(headers, key=lambda header: header.plan_date or datetime.min.replace(tzinfo=timezone.utc))
    headers_by_ref = {header.ref_key: header for header in headers}
    current_rows = [
        row
        for row in resolved.rows
        if (row.month_key or "").strip() == current_month
    ]
    matrix_view = build_production_plan_matrices(current_rows)
    current_qty_sum = sum(float(row.qty or 0.0) for row in current_rows)
    if current_rows and current_qty_sum <= 0:
        matrix = (matrix_view.get("matrices") or {}).get(current_month)
        if isinstance(matrix, dict):
            matrix["note"] = (
                "Объединённый план за месяц загружен из 1С, но OData вернула "
                "нулевые значения в полях «Количество»/«КоличествоУпаковок». "
                "Данные из файлов пользователя здесь не подмешиваются."
            )
    current_source = resolved.month_sources.get(current_month)
    values = [
        ["Строка", "Месяц", "Код", "Номенклатура", "Количество", "Ед.", "Подразделение", "Документ"],
        *[
            [
                str(row.line_number),
                row.month_key,
                row.nomenclature_code,
                row.nomenclature_name,
                f"{row.qty:g}",
                row.unit,
                row.department,
                headers_by_ref.get(row.plan_ref_key).number
                if row.plan_ref_key in headers_by_ref
                else (
                    resolved.month_sources.get(
                        row.month_key,
                        MonthPlanSource("", "", None, None, None),
                    ).number
                    if row.month_key in resolved.month_sources
                    else ""
                ),
            ]
            for row in current_rows
        ],
    ]
    return {
        "ok": True,
        "year": target_year,
        "message": (
            f"Актуальный план производства за {current_month}: "
            f"{len(current_rows)} строк, документов в БД: {len(headers)}"
        ),
        "source": primary_header.source_entity or "onec_production_plan_items",
        "count": len(current_rows),
        "documents_count": len(headers),
        "header": {
            "ref_key": primary_header.ref_key,
            "number": primary_header.number,
            "date": primary_header.plan_date.isoformat() if primary_header.plan_date else "",
            "posted": primary_header.posted,
            "deletion_mark": primary_header.deletion_mark,
            "period_start": primary_header.period_start.isoformat() if primary_header.period_start else "",
            "period_end": primary_header.period_end.isoformat() if primary_header.period_end else "",
        },
        "values": values,
        "matrix_view": matrix_view,
        "month_sources": {
            current_month: current_source.to_dict()
        } if current_source is not None else {},
        "gaps": resolved.gaps,
        "table_entities": sorted({header.source_entity for header in headers if header.source_entity}),
    }
