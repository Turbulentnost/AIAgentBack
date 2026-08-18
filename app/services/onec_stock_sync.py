"""TEMP/Aveon: выгрузка остатков из 1С OData и запись в PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from urllib.parse import quote

import requests
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.onec_odata import (
    create_session,
    format_onec_request_error,
    get_json,
    get_odata_base_url,
)
from app.models.onec_stock import OnecStockBalance, OnecStockSyncRun

EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
PAGE_SIZE = 1000
LOOKUP_BATCH = 40


def _onec_base() -> str:
    return get_odata_base_url().rstrip("/")


def _fetch_all_balances(http: requests.Session) -> list[dict]:
    """Все строки Balance без фильтра — полный срез регистра ТоварыНаСкладах."""
    base = _onec_base()
    rows: list[dict] = []
    skip = 0
    while True:
        url = (
            f"{base}/AccumulationRegister_ТоварыНаСкладах/Balance"
            f"?$top={PAGE_SIZE}&$skip={skip}&$format=json"
        )
        batch = get_json(http, url).get("value") or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        skip += len(batch)
    return rows


def _fetch_balances_at_date(http: requests.Session, as_of: date) -> list[dict]:
    """Срез регистра ТоварыНаСкладах на начало указанной даты."""
    base = _onec_base()
    period = datetime.combine(as_of, time.min).strftime("%Y-%m-%dT%H:%M:%S")
    rows: list[dict] = []
    skip = 0
    while True:
        url = (
            f"{base}/AccumulationRegister_ТоварыНаСкладах"
            f"/Balance(Period=datetime'{period}')"
            f"?$top={PAGE_SIZE}&$skip={skip}&$format=json"
        )
        batch = get_json(http, url).get("value") or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        skip += len(batch)
    return rows


def _batch_lookup(
    http: requests.Session,
    catalog: str,
    keys: list[str],
    fields: str,
) -> dict[str, dict]:
    result: dict[str, dict] = {}
    unique = [k for k in dict.fromkeys(keys) if k and k != EMPTY_GUID]
    for i in range(0, len(unique), LOOKUP_BATCH):
        chunk = unique[i : i + LOOKUP_BATCH]
        filt = " or ".join(f"Ref_Key eq guid'{key}'" for key in chunk)
        url = (
            f"{_onec_base()}/{catalog}"
            f"?$filter={quote(filt, safe='')}"
            f"&$select={fields}&$format=json"
        )
        for row in get_json(http, url).get("value") or []:
            ref = str(row.get("Ref_Key") or "")
            if ref:
                result[ref] = row
    return result


def fetch_stock_items_from_onec() -> dict:
    """Тянет все остатки из 1С и резолвит названия. Без записи в БД."""
    base = _onec_base()
    http = create_session()
    try:
        raw_rows = _fetch_all_balances(http)
        nom_keys = [str(r.get("Номенклатура_Key") or "") for r in raw_rows]
        wh_keys = [str(r.get("Склад_Key") or "") for r in raw_rows]
        from concurrent.futures import ThreadPoolExecutor

        def _lookup_nom() -> dict[str, dict]:
            session = create_session()
            try:
                return _batch_lookup(
                    session, "Catalog_Номенклатура", nom_keys, "Ref_Key,Code,Description"
                )
            finally:
                session.close()

        def _lookup_wh() -> dict[str, dict]:
            session = create_session()
            try:
                return _batch_lookup(session, "Catalog_Склады", wh_keys, "Ref_Key,Description")
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            nom_future = pool.submit(_lookup_nom)
            wh_future = pool.submit(_lookup_wh)
            nom_map = nom_future.result()
            wh_map = wh_future.result()

        items: list[dict] = []
        for row in raw_rows:
            in_stock = float(row.get("ВНаличииBalance") or 0)
            to_ship = float(row.get("КОтгрузкеBalance") or 0)
            nom_key = str(row.get("Номенклатура_Key") or "")
            wh_key = str(row.get("Склад_Key") or "")
            nom = nom_map.get(nom_key) or {}
            wh = wh_map.get(wh_key) or {}
            items.append(
                {
                    "code": str(nom.get("Code") or ""),
                    "name": str(nom.get("Description") or "").strip(),
                    "warehouse": str(wh.get("Description") or "").strip(),
                    "in_stock": in_stock,
                    "to_ship": to_ship,
                    "available": in_stock - to_ship,
                    "nomenclature_key": nom_key,
                    "characteristic_key": str(row.get("Характеристика_Key") or EMPTY_GUID),
                    "purpose_key": str(row.get("Назначение_Key") or EMPTY_GUID),
                    "warehouse_key": wh_key,
                    "room_key": str(row.get("Помещение_Key") or EMPTY_GUID),
                    "series_key": str(row.get("Серия_Key") or EMPTY_GUID),
                    "batch_key": str(row.get("КонтролируемаяПартия_Key") or EMPTY_GUID),
                }
            )

        items.sort(key=lambda x: (x["name"].lower(), x["warehouse"].lower()))
        positive = sum(1 for x in items if x["in_stock"] > 0)
        negative = sum(1 for x in items if x["in_stock"] < 0)
        return {
            "ok": True,
            "message": (
                f"Остатки из 1С: всего {len(items)} "
                f"(в наличии > 0: {positive}, < 0: {negative})"
            ),
            "status_code": 200,
            "url": f"{base}/AccumulationRegister_ТоварыНаСкладах/Balance",
            "base": base,
            "source": "AccumulationRegister_ТоварыНаСкладах/Balance",
            "count": len(items),
            "positive_count": positive,
            "negative_count": negative,
            "items": items,
        }
    except (requests.RequestException, RuntimeError, ValueError, TypeError) as exc:
        detail = format_onec_request_error(exc, base_url=base)
        return {
            "ok": False,
            "message": f"Не удалось получить остатки из 1С: {detail}",
            "status_code": None,
            "url": f"{base}/AccumulationRegister_ТоварыНаСкладах/Balance",
            "base": base,
            "source": "AccumulationRegister_ТоварыНаСкладах/Balance",
            "count": 0,
            "positive_count": 0,
            "negative_count": 0,
            "items": [],
        }
    finally:
        http.close()


def fetch_stock_snapshots_from_onec(dates: list[date]) -> dict:
    """Тянет срезы остатков на начало дат из 1С без записи в БД."""
    base = _onec_base()
    unique_dates = sorted({item for item in dates if isinstance(item, date)})
    if not unique_dates:
        return {
            "ok": True,
            "source": "AccumulationRegister_ТоварыНаСкладах/Balance",
            "dates": {},
            "count": 0,
        }

    http = create_session()
    try:
        raw_by_date: dict[str, list[dict]] = {}
        nom_keys: list[str] = []
        wh_keys: list[str] = []
        for as_of in unique_dates:
            raw_rows = _fetch_balances_at_date(http, as_of)
            raw_by_date[as_of.isoformat()] = raw_rows
            nom_keys.extend(str(r.get("Номенклатура_Key") or "") for r in raw_rows)
            wh_keys.extend(str(r.get("Склад_Key") or "") for r in raw_rows)

        from concurrent.futures import ThreadPoolExecutor

        def _lookup_nom() -> dict[str, dict]:
            session = create_session()
            try:
                return _batch_lookup(
                    session, "Catalog_Номенклатура", nom_keys, "Ref_Key,Code,Description"
                )
            finally:
                session.close()

        def _lookup_wh() -> dict[str, dict]:
            session = create_session()
            try:
                return _batch_lookup(session, "Catalog_Склады", wh_keys, "Ref_Key,Description")
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            nom_future = pool.submit(_lookup_nom)
            wh_future = pool.submit(_lookup_wh)
            nom_map = nom_future.result()
            wh_map = wh_future.result()

        snapshots: dict[str, list[dict]] = {}
        for day_key, raw_rows in raw_by_date.items():
            items: list[dict] = []
            for row in raw_rows:
                in_stock = float(row.get("ВНаличииBalance") or 0)
                to_ship = float(row.get("КОтгрузкеBalance") or 0)
                nom_key = str(row.get("Номенклатура_Key") or "")
                wh_key = str(row.get("Склад_Key") or "")
                nom = nom_map.get(nom_key) or {}
                wh = wh_map.get(wh_key) or {}
                items.append(
                    {
                        "code": str(nom.get("Code") or ""),
                        "name": str(nom.get("Description") or "").strip(),
                        "warehouse": str(wh.get("Description") or "").strip(),
                        "in_stock": in_stock,
                        "to_ship": to_ship,
                        "available": in_stock - to_ship,
                        "nomenclature_key": nom_key,
                        "warehouse_key": wh_key,
                    }
                )
            snapshots[day_key] = items

        return {
            "ok": True,
            "source": "AccumulationRegister_ТоварыНаСкладах/Balance",
            "dates": snapshots,
            "count": sum(len(items) for items in snapshots.values()),
        }
    except (requests.RequestException, RuntimeError, ValueError, TypeError) as exc:
        detail = format_onec_request_error(exc, base_url=base)
        return {
            "ok": False,
            "message": f"Не удалось получить срезы остатков из 1С: {detail}",
            "source": "AccumulationRegister_ТоварыНаСкладах/Balance",
            "dates": {},
            "count": 0,
        }
    finally:
        http.close()


async def ensure_onec_stock_tables(db: AsyncSession | None = None) -> None:
    """Создаёт таблицы остатков, если alembic-цепочка локально сломана."""
    from app.services.onec_db_schema import ensure_onec_agent_tables

    await ensure_onec_agent_tables()


async def replace_stock_in_db(db: AsyncSession, items: list[dict]) -> dict:
    """Полная перезапись остатков в БД (снимок = то, что сейчас в 1С)."""
    await ensure_onec_stock_tables(db)
    started = datetime.now(timezone.utc)
    run = OnecStockSyncRun(
        id=uuid.uuid4(),
        source="AccumulationRegister_ТоварыНаСкладах/Balance",
        status="running",
        fetched_count=len(items),
        saved_count=0,
        positive_count=sum(1 for x in items if float(x.get("in_stock") or 0) > 0),
        negative_count=sum(1 for x in items if float(x.get("in_stock") or 0) < 0),
        started_at=started,
    )
    db.add(run)
    await db.flush()

    await db.execute(delete(OnecStockBalance))
    now = datetime.now(timezone.utc)
    rows = [
        OnecStockBalance(
            id=uuid.uuid4(),
            code=str(item.get("code") or ""),
            name=str(item.get("name") or ""),
            warehouse=str(item.get("warehouse") or ""),
            in_stock=float(item.get("in_stock") or 0),
            to_ship=float(item.get("to_ship") or 0),
            available=float(item.get("available") or 0),
            nomenclature_key=str(item.get("nomenclature_key") or ""),
            characteristic_key=str(item.get("characteristic_key") or EMPTY_GUID),
            purpose_key=str(item.get("purpose_key") or EMPTY_GUID),
            warehouse_key=str(item.get("warehouse_key") or ""),
            room_key=str(item.get("room_key") or EMPTY_GUID),
            series_key=str(item.get("series_key") or EMPTY_GUID),
            batch_key=str(item.get("batch_key") or EMPTY_GUID),
            synced_at=now,
        )
        for item in items
    ]
    db.add_all(rows)
    run.saved_count = len(rows)
    run.status = "ok"
    run.finished_at = datetime.now(timezone.utc)
    await db.flush()

    db_count = await db.scalar(select(func.count()).select_from(OnecStockBalance))
    return {
        "sync_run_id": str(run.id),
        "saved_count": len(rows),
        "db_count": int(db_count or 0),
    }


async def list_stock_balances_from_db(
    db: AsyncSession,
    *,
    query: str | None = None,
    warehouse: str | None = None,
    limit: int = 5000,
    offset: int = 0,
    spec_materials_only: bool = False,
) -> dict:
    """Остатки из БД для просмотра в UI (после sync из 1С)."""
    from app.services.spec_nomenclature_match import load_spec_nomenclature_index, stock_row_matches_spec

    await ensure_onec_stock_tables(db)
    stmt = select(OnecStockBalance).order_by(
        OnecStockBalance.warehouse, OnecStockBalance.code, OnecStockBalance.name
    )
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(
            (OnecStockBalance.code.ilike(like))
            | (OnecStockBalance.name.ilike(like))
            | (OnecStockBalance.warehouse.ilike(like))
        )
    if warehouse:
        stmt = stmt.where(OnecStockBalance.warehouse == warehouse)

    rows = (await db.execute(stmt)).scalars().all()
    total_all = len(rows)
    spec_index = None
    if spec_materials_only:
        spec_index = await load_spec_nomenclature_index(db)
        rows = [row for row in rows if stock_row_matches_spec(row, spec_index)]

    total = len(rows)
    page_rows = rows[offset : offset + limit]
    synced_at = page_rows[0].synced_at.isoformat() if page_rows and page_rows[0].synced_at else None
    if synced_at is None and rows:
        synced_at = rows[0].synced_at.isoformat() if rows[0].synced_at else None

    return {
        "ok": True,
        "total": total,
        "total_all": total_all if spec_materials_only else total,
        "spec_materials_only": spec_materials_only,
        "spec_nomenclature_count": spec_index.size if spec_index else 0,
        "limit": limit,
        "offset": offset,
        "synced_at": synced_at,
        "items": [
            {
                "code": r.code,
                "name": r.name,
                "warehouse": r.warehouse,
                "in_stock": r.in_stock,
                "to_ship": r.to_ship,
                "available": r.available,
                "nomenclature_key": r.nomenclature_key,
                "warehouse_key": r.warehouse_key,
            }
            for r in page_rows
        ],
    }


async def get_stock_sync_status(db: AsyncSession, *, ensure: bool = True) -> dict:
    """Последняя успешная (или любая) выгрузка остатков для UI."""
    if ensure:
        await ensure_onec_stock_tables(db)
    latest_run = await db.scalar(
        select(OnecStockSyncRun).order_by(OnecStockSyncRun.finished_at.desc().nullslast()).limit(1)
    )
    db_count = int(await db.scalar(select(func.count()).select_from(OnecStockBalance)) or 0)
    if latest_run is None:
        return {
            "last_sync_at": None,
            "status": None,
            "saved_count": 0,
            "db_count": db_count,
            "positive_count": 0,
            "negative_count": 0,
            "error_message": None,
        }
    return {
        "last_sync_at": latest_run.finished_at.isoformat() if latest_run.finished_at else None,
        "status": latest_run.status,
        "saved_count": latest_run.saved_count,
        "db_count": db_count,
        "positive_count": latest_run.positive_count,
        "negative_count": latest_run.negative_count,
        "error_message": latest_run.error_message,
    }
