from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.integration.deps import IntegrationPrincipal, require_permission
from app.config import settings
from app.gost.catalog import GOST_LINE_ORDER
from app.integration.job_service import IntegrationJobService
from app.models.integration import IntegrationJob
from app.schemas.integration import RulesetInfo, RulesetsResponse

router = APIRouter(prefix="/api/v1", tags=["integration"])


@router.get("/health")
async def integration_health(db: AsyncSession = Depends(get_db)) -> dict:
    queued = int(
        (await db.scalar(select(func.count()).select_from(IntegrationJob).where(IntegrationJob.status == "queued")))
        or 0
    )
    accepted = int(
        (await db.scalar(select(func.count()).select_from(IntegrationJob).where(IntegrationJob.status == "accepted")))
        or 0
    )
    return {
        "status": "ok",
        "integration_worker_enabled": settings.integration_worker_enabled,
        "queue": {"accepted": accepted, "queued": queued},
        "closed_contour": settings.closed_contour,
    }


@router.get("/rulesets", response_model=RulesetsResponse)
async def list_rulesets(
    _: IntegrationPrincipal = Depends(require_permission("checks:read")),
) -> RulesetsResponse:
    current = IntegrationJobService.RULESET_VERSION
    items = [
        RulesetInfo(
            version=current,
            title="ЕСКD Agent ruleset",
            effective_from="2026-04-01",
        )
    ]
    for key, title in GOST_LINE_ORDER:
        items.append(RulesetInfo(version=f"GOST-{key}", title=title))
    return RulesetsResponse(items=items, current=current)
