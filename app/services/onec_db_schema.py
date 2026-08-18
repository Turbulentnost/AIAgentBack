"""DDL для таблиц синхронизации 1С (Aveon) — один раз за процесс."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

_tables_ready = False
_tables_lock = asyncio.Lock()

# Кросс-процессная сериализация bootstrap DDL (uvicorn workers / celery).
_ONEC_SCHEMA_ADVISORY_LOCK_KEY = 865_000_001


async def ensure_onec_agent_tables() -> None:
    """Создаёт таблицы 1С один раз; схема колонок — через Alembic, без runtime ALTER."""
    global _tables_ready
    if _tables_ready:
        return
    async with _tables_lock:
        if _tables_ready:
            return
        from app.db.base import Base
        from app.db.session import engine
        from app.models.onec_nomenclature import OnecNomenclature
        from app.models.onec_resource_spec import (
            OnecResourceSpec,
            OnecResourceSpecMaterial,
            OnecResourceSpecOutput,
            OnecResourceSpecSyncRun,
        )
        from app.models.onec_production_plan import (
            OnecProductionPlanHeader,
            OnecProductionPlanItem,
            OnecProductionPlanSyncRun,
        )
        from app.models.onec_stock import OnecStockBalance, OnecStockSyncRun

        def _create(sync_conn) -> None:
            Base.metadata.create_all(
                sync_conn,
                tables=[
                    OnecResourceSpec.__table__,
                    OnecResourceSpecMaterial.__table__,
                    OnecResourceSpecOutput.__table__,
                    OnecResourceSpecSyncRun.__table__,
                    OnecNomenclature.__table__,
                    OnecProductionPlanHeader.__table__,
                    OnecProductionPlanItem.__table__,
                    OnecProductionPlanSyncRun.__table__,
                    OnecStockBalance.__table__,
                    OnecStockSyncRun.__table__,
                ],
                checkfirst=True,
            )

        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": _ONEC_SCHEMA_ADVISORY_LOCK_KEY},
            )
            await conn.run_sync(_create)
        _tables_ready = True
