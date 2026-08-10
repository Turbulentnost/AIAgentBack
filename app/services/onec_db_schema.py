"""DDL для таблиц синхронизации 1С (Aveon) — один раз за процесс."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

_tables_ready = False
_tables_lock = asyncio.Lock()


async def ensure_onec_agent_tables() -> None:
    """Создаёт/мигрирует таблицы 1С один раз, без повторного захвата пула на каждый запрос."""
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
                    OnecStockBalance.__table__,
                    OnecStockSyncRun.__table__,
                ],
                checkfirst=True,
            )
            for stmt in (
                "ALTER TABLE onec_resource_spec_materials "
                "ADD COLUMN IF NOT EXISTS unit VARCHAR(64) NOT NULL DEFAULT ''",
                "ALTER TABLE onec_nomenclature "
                "ADD COLUMN IF NOT EXISTS unit_key VARCHAR(64) NOT NULL DEFAULT ''",
                "ALTER TABLE onec_nomenclature "
                "ADD COLUMN IF NOT EXISTS unit VARCHAR(64) NOT NULL DEFAULT ''",
            ):
                sync_conn.execute(text(stmt))

        async with engine.begin() as conn:
            await conn.run_sync(_create)
        _tables_ready = True
