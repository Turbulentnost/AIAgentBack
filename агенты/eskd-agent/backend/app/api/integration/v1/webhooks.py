from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.integration.deps import IntegrationPrincipal, require_permission
from app.integration.webhook_service import WebhookService
from app.schemas.integration import WebhookCreate, WebhookRead

router = APIRouter(prefix="/api/v1/webhooks", tags=["integration-webhooks"])


@router.post("", response_model=WebhookRead)
async def register_webhook(
    body: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    _: IntegrationPrincipal = Depends(require_permission("webhooks:write")),
):
    row = await WebhookService(db).register(
        name=body.name,
        url=body.url,
        events=body.events,
        secret=body.secret,
        source_system=body.source_system,
    )
    return WebhookRead(
        id=row.id,
        name=row.name,
        url=row.url,
        events=list(row.events or []),
        enabled=row.enabled,
        source_system=row.source_system,
    )


@router.get("", response_model=list[WebhookRead])
async def list_webhooks(
    db: AsyncSession = Depends(get_db),
    _: IntegrationPrincipal = Depends(require_permission("webhooks:write")),
):
    rows = await WebhookService(db).list_webhooks()
    return [
        WebhookRead(
            id=row.id,
            name=row.name,
            url=row.url,
            events=list(row.events or []),
            enabled=row.enabled,
            source_system=row.source_system,
        )
        for row in rows
    ]
