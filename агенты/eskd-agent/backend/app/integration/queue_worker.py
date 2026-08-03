from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.db.session import SessionLocal
from app.integration.file_adapter import FileExchangeAdapter
from app.integration.webhook_service import WebhookService

_log = logging.getLogger("eskd.integration.worker")


class IntegrationQueueWorker:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if not settings.integration_worker_enabled:
            _log.info("integration worker disabled")
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())
        _log.info("integration worker started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                async with SessionLocal() as db:
                    imported = await FileExchangeAdapter(db).scan_incoming()
                    delivered = await WebhookService(db).deliver_pending()
                if imported or delivered:
                    _log.info("worker cycle: imported=%s webhooks=%s", imported, delivered)
            except Exception as exc:
                _log.warning("worker cycle failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=settings.integration_poll_sec)
            except asyncio.TimeoutError:
                continue
