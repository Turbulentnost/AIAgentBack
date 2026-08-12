from __future__ import annotations

import asyncio

_tables_ready = False
_tables_lock = asyncio.Lock()


async def ensure_aveon_shipment_schedule_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    async with _tables_lock:
        if _tables_ready:
            return
        from app.db.base import Base
        from app.db.session import engine
        from app.models.aveon_shipment_schedule import (
            AveonShipmentScheduleChangeEvent,
            AveonShipmentScheduleVersion,
        )

        def _create(sync_conn) -> None:
            Base.metadata.create_all(
                sync_conn,
                tables=[
                    AveonShipmentScheduleVersion.__table__,
                    AveonShipmentScheduleChangeEvent.__table__,
                ],
                checkfirst=True,
            )

        async with engine.begin() as conn:
            await conn.run_sync(_create)
        _tables_ready = True
