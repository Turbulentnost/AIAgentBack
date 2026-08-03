from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.integration.deps import IntegrationPrincipal, require_permission
from app.integration.auth_service import AuthService
from app.integration.exchange_log_service import ExchangeLogService
from app.schemas.integration import ApiKeyCreateResponse, ExchangeLogItem, ExchangeLogListResponse

router = APIRouter(prefix="/api/v1", tags=["integration-admin"])


@router.get("/integration/exchange-log", response_model=ExchangeLogListResponse)
async def list_exchange_log(
    page: int = 1,
    size: int = 50,
    request_id: str | None = None,
    source_system: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: IntegrationPrincipal = Depends(require_permission("logs:read")),
):
    rows, total = await ExchangeLogService(db).list_logs(
        page=page,
        size=size,
        request_id=request_id,
        source_system=source_system,
    )
    return ExchangeLogListResponse(
        items=[
            ExchangeLogItem(
                id=row.id,
                occurred_at=row.occurred_at,
                sender=row.sender,
                receiver=row.receiver,
                request_id=row.request_id,
                operation=row.operation,
                result=row.result,
                error_message=row.error_message,
                designation=row.designation,
                revision=row.revision,
                actor=row.actor,
            )
            for row in rows
        ],
        total=total,
    )


@router.post("/integration/api-keys", response_model=ApiKeyCreateResponse)
async def create_api_key(
    name: str,
    roles: str = "ESKD_Designers",
    db: AsyncSession = Depends(get_db),
    _: IntegrationPrincipal = Depends(require_permission("keys:write")),
):
    role_list = [r.strip() for r in roles.split(",") if r.strip()]
    row, raw = await AuthService(db).create_api_key(name=name, roles=role_list)
    return ApiKeyCreateResponse(id=row.id, name=row.name, api_key=raw, roles=role_list)
