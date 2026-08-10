"""TEMP/Aveon: выгрузка остатков из 1С OData и запись в PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from requests.auth import HTTPBasicAuth
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.onec_odata import normalize_odata_base
from app.models.onec_stock import OnecStockBalance, OnecStockSyncRun

ONEC_BASE = normalize_odata_base("http://26.169.32.56/ERP2").rstrip("/")
ONEC_USER = "odata.user"
ONEC_PASSWORD = "npo852456"
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
PAGE_SIZE = 1000
LOOKUP_BATCH = 40
TIMEOUT_SEC = 120


def _get_json(session: requests.Session, url: str) -> dict:
    response = session.get(
        url,
        timeout=TIMEOUT_SEC,
        headers={"Accept": "application/json"},
    )
    response.encoding = "utf-8"
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {(response.text or '')[:300]}")
    return response.json()


def _fetch_all_balances(http: requests.Session) -> list[dict]:
    """Все строки Balance без фильтра — полный срез регистра ТоварыНаСкладах."""
    rows: list[dict] = []
    skip = 0
    while True:
        url = (
            f"{ONEC_BASE}/AccumulationRegister_ТоварыНаСкладах/Balance"
            f"?$top={PAGE_SIZE}&$skip={skip}&$format=json"
        )
        batch = _get_json(http, url).get("value") or []
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
            f"{ONEC_BASE}/{catalog}"
            f"?$filter={quote(filt, safe='')}"
            f"&$select={fields}&$format=json"
        )
        for row in _get_json(http, url).get("value") or []:
            ref = str(row.get("Ref_Key") or "")
            if ref:
                result[ref] = row
    return result


def fetch_stock_items_from_onec() -> dict:
    """Тянет все остатки из 1С и резолвит названия. Без записи в БД."""
    http = requests.Session()
    http.auth = HTTPBasicAuth(ONEC_USER, ONEC_PASSWORD)
    try:
        raw_rows = _fetch_all_balances(http)
        nom_keys = [str(r.get("Номенклатура_Key") or "") for r in raw_rows]
        wh_keys = [str(r.get("Склад_Key") or "") for r in raw_rows]
        nom_map = _batch_lookup(
            http, "Catalog_Номенклатура", nom_keys, "Ref_Key,Code,Description"
        )
        wh_map = _batch_lookup(http, "Catalog_Склады", wh_keys, "Ref_Key,Description")

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
            "url": f"{ONEC_BASE}/AccumulationRegister_ТоварыНаСкладах/Balance",
            "base": ONEC_BASE,
            "source": "AccumulationRegister_ТоварыНаСкладах/Balance",
            "count": len(items),
            "positive_count": positive,
            "negative_count": negative,
            "items": items,
        }
    except (requests.RequestException, RuntimeError, ValueError, TypeError) as exc:
        return {
            "ok": False,
            "message": f"Не удалось получить остатки из 1С: {exc}",
            "status_code": None,
            "url": f"{ONEC_BASE}/AccumulationRegister_ТоварыНаСкладах/Balance",
            "base": ONEC_BASE,
            "source": "AccumulationRegister_ТоварыНаСкладах/Balance",
            "count": 0,
            "positive_count": 0,
            "negative_count": 0,
            "items": [],
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
