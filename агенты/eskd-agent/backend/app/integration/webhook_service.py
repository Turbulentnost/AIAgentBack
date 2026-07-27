from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.integration import IntegrationJob, IntegrationWebhook, IntegrationWebhookDelivery


class WebhookService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def register(
        self,
        *,
        name: str,
        url: str,
        events: list[str],
        secret: str | None = None,
        source_system: str | None = None,
    ) -> IntegrationWebhook:
        row = IntegrationWebhook(
            name=name,
            url=url,
            events=events,
            secret=secret,
            source_system=source_system,
        )
        self._db.add(row)
        await self._db.commit()
        await self._db.refresh(row)
        return row

    async def list_webhooks(self) -> list[IntegrationWebhook]:
        return list((await self._db.scalars(select(IntegrationWebhook))).all())

    async def enqueue_for_job(self, job: IntegrationJob, summary: dict) -> None:
        event = "CheckRejected" if job.blocks_workflow else "CheckCompleted"
        webhooks = (
            await self._db.scalars(
                select(IntegrationWebhook).where(
                    IntegrationWebhook.enabled.is_(True),
                )
            )
        ).all()
        for hook in webhooks:
            events = hook.events or []
            if event not in events and "*" not in events:
                continue
            if hook.source_system and hook.source_system != job.source_system:
                continue
            payload = {
                "event": event,
                "check_id": str(job.id),
                "request_id": job.request_id,
                "status": job.status,
                "result_status": job.result_status,
                "critical_count": job.critical_count,
                "major_count": job.major_count,
                "minor_count": job.minor_count,
                "blocks_workflow": job.blocks_workflow,
                "ruleset_version": job.ruleset_version,
                "report_url": summary.get("report_url"),
                "checked_at": job.completed_at.isoformat() if job.completed_at else None,
            }
            delivery = IntegrationWebhookDelivery(
                webhook_id=hook.id,
                job_id=job.id,
                event=event,
                payload=payload,
                status="pending",
                next_retry_at=datetime.now(timezone.utc),
            )
            self._db.add(delivery)
        await self._db.commit()

    async def deliver_pending(self, *, limit: int = 20) -> int:
        now = datetime.now(timezone.utc)
        rows = (
            await self._db.scalars(
                select(IntegrationWebhookDelivery)
                .where(
                    IntegrationWebhookDelivery.status.in_(["pending", "retry"]),
                    IntegrationWebhookDelivery.next_retry_at <= now,
                )
                .limit(limit)
            )
        ).all()
        sent = 0
        for delivery in rows:
            hook = await self._db.get(IntegrationWebhook, delivery.webhook_id)
            if not hook or not hook.enabled:
                delivery.status = "skipped"
                await self._db.commit()
                continue
            ok, err = await self._post(hook, delivery.payload)
            delivery.attempts += 1
            if ok:
                delivery.status = "sent"
                delivery.last_error = None
                sent += 1
            else:
                delivery.status = "retry" if delivery.attempts < settings.webhook_max_retries else "failed"
                delivery.last_error = err
                delay = min(3600, 2 ** delivery.attempts)
                delivery.next_retry_at = now + timedelta(seconds=delay)
            await self._db.commit()
        return sent

    async def _post(self, hook: IntegrationWebhook, payload: dict) -> tuple[bool, str | None]:
        body = json.dumps(payload, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json"}
        if hook.secret:
            sig = hmac.new(hook.secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-ESKD-Signature"] = f"sha256={sig}"
        try:
            async with httpx.AsyncClient(timeout=settings.webhook_timeout_sec) as client:
                resp = await client.post(hook.url, content=body, headers=headers)
            if resp.status_code >= 400:
                return False, f"HTTP {resp.status_code}: {resp.text[:500]}"
            return True, None
        except Exception as exc:
            return False, str(exc)
