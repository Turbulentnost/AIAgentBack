from __future__ import annotations

import asyncio
import base64
import ipaddress
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.documents.storage import object_storage
from app.models.browser_run import BrowserRun
from app.models.enums import BrowserRunStatus
from app.schemas.browser_run import BrowserRunCreate, BrowserRunResult


EXECUTABLE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".js",
    ".msi",
    ".ps1",
    ".scr",
    ".sh",
    ".vbs",
}


class BrowserRunnerError(ValueError):
    pass


class BrowserRunnerService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def validate_url(self, url: str, *, allow_any_domain: bool = False) -> None:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        blocked_schemes = {item.rstrip(":").lower() for item in settings.browser_blocked_schemes}
        if not scheme or scheme in blocked_schemes or scheme not in {"http", "https"}:
            raise BrowserRunnerError("URL должен использовать разрешенную схему http или https")

        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            raise BrowserRunnerError("URL должен содержать домен")
        if host in {"localhost"} or host.endswith(".localhost"):
            raise BrowserRunnerError("Локальные адреса запрещены для browser-runner")

        # Внутренние/loopback/private IP блокируются всегда, даже в open-web режиме.
        self._validate_ip_host(host)
        if not allow_any_domain and not self._is_allowed_domain(host):
            raise BrowserRunnerError("Домен не входит в allowlist browser-runner")

        suffix = PurePosixPath(parsed.path or "").suffix.lower()
        if suffix in EXECUTABLE_SUFFIXES:
            raise BrowserRunnerError("Открытие исполняемых файлов запрещено")

    async def create_run(
        self,
        payload: BrowserRunCreate,
        *,
        requested_by_user_id: uuid.UUID,
        requested_by_agent_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        allow_any_domain: bool = False,
    ) -> BrowserRun:
        self.validate_url(payload.url, allow_any_domain=allow_any_domain)
        timeout = min(payload.timeout_seconds, settings.BROWSER_MAX_TIMEOUT_SECONDS)
        run = BrowserRun(
            requested_by_agent_id=requested_by_agent_id or payload.agent_id,
            requested_by_user_id=requested_by_user_id,
            task_id=task_id or payload.task_id,
            url=payload.url,
            method="GET",
            extract_mode=payload.extract_mode,
            status=BrowserRunStatus.PENDING,
            timeout_seconds=timeout,
            metadata_={"reason": payload.reason},
        )
        self.db.add(run)
        await self.db.flush()
        return run

    async def list_pending_for_user(self, user_id: uuid.UUID) -> list[BrowserRun]:
        result = await self.db.execute(
            select(BrowserRun)
            .where(
                BrowserRun.requested_by_user_id == user_id,
                BrowserRun.status.in_([BrowserRunStatus.PENDING, BrowserRunStatus.RUNNING]),
            )
            .order_by(BrowserRun.created_at.asc())
        )
        runs = list(result.scalars().all())
        for run in runs:
            if run.status == BrowserRunStatus.PENDING:
                run.status = BrowserRunStatus.RUNNING
        await self.db.flush()
        return runs

    async def submit_result(self, run_id: uuid.UUID, user_id: uuid.UUID, payload: BrowserRunResult) -> BrowserRun:
        run = await self.db.get(BrowserRun, run_id)
        if run is None or run.requested_by_user_id != user_id:
            raise BrowserRunnerError("BrowserRun не найден или недоступен текущему пользователю")
        if run.status in {BrowserRunStatus.COMPLETED, BrowserRunStatus.FAILED, BrowserRunStatus.TIMEOUT, BrowserRunStatus.CANCELLED}:
            return run

        run.status = payload.status
        run.title = payload.title
        run.result_text = payload.text
        run.result_html = payload.html
        run.result_tables = [table.model_dump() for table in payload.tables]
        run.error_message = payload.error_message
        run.finished_at = _now()
        metadata = dict(run.metadata_ or {})
        metadata.update(payload.metadata or {})
        run.metadata_ = metadata
        if payload.screenshot_data_url:
            run.screenshot_object_name = self._store_screenshot(run.id, payload.screenshot_data_url)
        if run.status == BrowserRunStatus.COMPLETED and run.error_message:
            run.status = BrowserRunStatus.FAILED
        await self.db.flush()
        return run

    async def get_run(self, run_id: uuid.UUID) -> BrowserRun | None:
        return await self.db.get(BrowserRun, run_id)

    async def wait_for_result(self, run_id: uuid.UUID, timeout_seconds: int) -> BrowserRun:
        deadline = asyncio.get_running_loop().time() + min(timeout_seconds, settings.BROWSER_MAX_TIMEOUT_SECONDS)
        terminal_statuses = {
            BrowserRunStatus.COMPLETED,
            BrowserRunStatus.FAILED,
            BrowserRunStatus.TIMEOUT,
            BrowserRunStatus.CANCELLED,
        }
        while asyncio.get_running_loop().time() < deadline:
            run = await self.db.get(BrowserRun, run_id, populate_existing=True)
            if run is None:
                raise BrowserRunnerError("BrowserRun не найден")
            if run.status in terminal_statuses:
                return run
            await asyncio.sleep(settings.BROWSER_POLL_INTERVAL_SECONDS)

        run = await self.db.get(BrowserRun, run_id, populate_existing=True)
        if run is None:
            raise BrowserRunnerError("BrowserRun не найден")
        run.status = BrowserRunStatus.TIMEOUT
        run.error_message = "Истекло время ожидания результата от браузера пользователя"
        run.finished_at = _now()
        await self.db.flush()
        return run

    def _validate_ip_host(self, host: str) -> None:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return
        if address.is_loopback or address.is_link_local or address.is_private or address.is_reserved:
            raise BrowserRunnerError("Внутренние технические IP-адреса запрещены")

    def _is_allowed_domain(self, host: str) -> bool:
        for domain in settings.browser_allowed_domains:
            normalized = domain.lower().strip().rstrip(".")
            if normalized == "*":
                return True
            if normalized.startswith("*.") and host.endswith(normalized[1:]):
                return True
            if host == normalized:
                return True
        return False

    def _store_screenshot(self, run_id: uuid.UUID, data_url: str) -> str:
        try:
            header, encoded = data_url.split(",", 1)
            content_type = header.removeprefix("data:").split(";", 1)[0] or "image/png"
            data = base64.b64decode(encoded)
        except Exception as exc:
            raise BrowserRunnerError("Некорректный screenshot data URL") from exc

        object_name = f"browser-runs/{run_id}/screenshot.png"
        object_storage.put_object(object_name, data, content_type)
        return object_name


def _now() -> datetime:
    return datetime.now(timezone.utc)
