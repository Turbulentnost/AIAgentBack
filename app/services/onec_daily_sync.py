"""Плановая синхронизация остатков и ресурсных спецификаций из 1С в PostgreSQL."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.onec_resource_spec_sync import (
    fetch_resource_specs_from_onec,
    replace_resource_specs_in_db,
)
from app.services.onec_stock_sync import fetch_stock_items_from_onec, replace_stock_in_db

logger = get_logger(__name__)


async def sync_onec_stock_to_db(db: AsyncSession) -> dict[str, Any]:
    payload = await asyncio.to_thread(fetch_stock_items_from_onec)
    if not payload.get("ok"):
        return {
            "step": "stock",
            "ok": False,
            "message": payload.get("message"),
            "count": payload.get("count") or 0,
        }

    saved = await replace_stock_in_db(db, payload.get("items") or [])
    db_match = saved["db_count"] == payload["count"]
    result = {
        "step": "stock",
        "ok": True,
        "message": payload.get("message"),
        "count": payload.get("count") or 0,
        "saved_count": saved["saved_count"],
        "db_count": saved["db_count"],
        "sync_run_id": saved["sync_run_id"],
        "db_match": db_match,
    }
    logger.info(
        "onec_daily_sync.stock.completed",
        count=result["count"],
        saved_count=result["saved_count"],
        db_match=db_match,
    )
    return result


async def sync_onec_resource_specs_to_db(db: AsyncSession) -> dict[str, Any]:
    payload = await asyncio.to_thread(fetch_resource_specs_from_onec)
    if not payload.get("ok"):
        return {
            "step": "resource_specs",
            "ok": False,
            "message": payload.get("message"),
            "count": payload.get("count") or 0,
        }

    specs = payload.pop("specs", [])
    nomenclature_items = payload.pop("nomenclature_items", [])
    saved = await replace_resource_specs_in_db(
        db, specs, nomenclature_items=nomenclature_items
    )
    db_match = (
        saved["db_specs"] == payload["count"]
        and saved["db_materials"] == payload["materials_count"]
        and saved["db_outputs"] == payload["outputs_count"]
    )
    result = {
        "step": "resource_specs",
        "ok": True,
        "message": payload.get("message"),
        "count": payload.get("count") or 0,
        "materials_count": payload.get("materials_count") or 0,
        "outputs_count": payload.get("outputs_count") or 0,
        "saved_specs": saved["saved_specs"],
        "saved_materials": saved["saved_materials"],
        "saved_outputs": saved["saved_outputs"],
        "db_specs": saved["db_specs"],
        "db_materials": saved["db_materials"],
        "db_outputs": saved["db_outputs"],
        "sync_run_id": saved["sync_run_id"],
        "db_match": db_match,
        "folder_path": payload.get("folder_path"),
    }
    logger.info(
        "onec_daily_sync.resource_specs.completed",
        specs=result["db_specs"],
        materials=result["db_materials"],
        outputs=result["db_outputs"],
        db_match=db_match,
    )
    return result


async def sync_onec_production_plan_step(db: AsyncSession) -> dict[str, Any]:
    # Keep the OData fetch off the event loop; DB writes stay on AsyncSession.
    from app.services.onec_production_plan_probe import fetch_latest_production_plan_from_onec
    from app.services.onec_production_plan_sync import replace_production_plan_in_db

    payload = await asyncio.to_thread(fetch_latest_production_plan_from_onec)
    if not payload.get("ok"):
        return {
            "step": "production_plan",
            "ok": False,
            "message": payload.get("message"),
            "count": payload.get("count") or 0,
        }
    saved = await replace_production_plan_in_db(db, payload)
    db_match = saved["db_count"] == payload["count"]
    result = {
        "step": "production_plan",
        "ok": True,
        "message": payload.get("message"),
        "count": payload.get("count") or 0,
        "saved_count": saved["saved_count"],
        "db_count": saved["db_count"],
        "sync_run_id": saved["sync_run_id"],
        "db_match": db_match,
        "plan_number": saved["plan_number"],
        "plan_date": saved["plan_date"],
    }
    logger.info(
        "onec_daily_sync.production_plan.completed",
        count=result["count"],
        saved_count=result["saved_count"],
        db_match=db_match,
        plan_number=result["plan_number"],
    )
    return result


async def run_onec_daily_sync() -> dict[str, Any]:
    from app.db.session import AsyncSessionLocal

    stock_payload = await asyncio.to_thread(fetch_stock_items_from_onec)
    if not stock_payload.get("ok"):
        stock_result: dict[str, Any] = {
            "step": "stock",
            "ok": False,
            "message": stock_payload.get("message"),
            "count": stock_payload.get("count") or 0,
        }
    else:
        async with AsyncSessionLocal() as db:
            saved = await replace_stock_in_db(db, stock_payload.get("items") or [])
            await db.commit()
        db_match = saved["db_count"] == stock_payload["count"]
        stock_result = {
            "step": "stock",
            "ok": True,
            "message": stock_payload.get("message"),
            "count": stock_payload.get("count") or 0,
            "saved_count": saved["saved_count"],
            "db_count": saved["db_count"],
            "sync_run_id": saved["sync_run_id"],
            "db_match": db_match,
        }
        logger.info(
            "onec_daily_sync.stock.completed",
            count=stock_result["count"],
            saved_count=stock_result["saved_count"],
            db_match=db_match,
        )

    specs_payload = await asyncio.to_thread(fetch_resource_specs_from_onec)
    if not specs_payload.get("ok"):
        specs_result: dict[str, Any] = {
            "step": "resource_specs",
            "ok": False,
            "message": specs_payload.get("message"),
            "count": specs_payload.get("count") or 0,
        }
    else:
        specs = specs_payload.pop("specs", [])
        nomenclature_items = specs_payload.pop("nomenclature_items", [])
        async with AsyncSessionLocal() as db:
            saved = await replace_resource_specs_in_db(
                db, specs, nomenclature_items=nomenclature_items
            )
            await db.commit()
        db_match = (
            saved["db_specs"] == specs_payload["count"]
            and saved["db_materials"] == specs_payload["materials_count"]
            and saved["db_outputs"] == specs_payload["outputs_count"]
        )
        specs_result = {
            "step": "resource_specs",
            "ok": True,
            "message": specs_payload.get("message"),
            "count": specs_payload.get("count") or 0,
            "materials_count": specs_payload.get("materials_count") or 0,
            "outputs_count": specs_payload.get("outputs_count") or 0,
            "saved_specs": saved["saved_specs"],
            "saved_materials": saved["saved_materials"],
            "saved_outputs": saved["saved_outputs"],
            "db_specs": saved["db_specs"],
            "db_materials": saved["db_materials"],
            "db_outputs": saved["db_outputs"],
            "sync_run_id": saved["sync_run_id"],
            "db_match": db_match,
            "folder_path": specs_payload.get("folder_path"),
        }
        logger.info(
            "onec_daily_sync.resource_specs.completed",
            specs=specs_result["db_specs"],
            materials=specs_result["db_materials"],
            outputs=specs_result["db_outputs"],
            db_match=db_match,
        )

    async with AsyncSessionLocal() as db:
        production_plan_result = await sync_onec_production_plan_step(db)
        await db.commit()

    ok = (
        bool(stock_result.get("ok"))
        and bool(specs_result.get("ok"))
        and bool(production_plan_result.get("ok"))
    )
    if not ok:
        logger.warning(
            "onec_daily_sync.failed",
            stock_ok=stock_result.get("ok"),
            specs_ok=specs_result.get("ok"),
            production_plan_ok=production_plan_result.get("ok"),
            stock_message=stock_result.get("message"),
            specs_message=specs_result.get("message"),
            production_plan_message=production_plan_result.get("message"),
        )
    else:
        logger.info("onec_daily_sync.completed")

    return {
        "ok": ok,
        "stock": stock_result,
        "resource_specs": specs_result,
        "production_plan": production_plan_result,
    }
