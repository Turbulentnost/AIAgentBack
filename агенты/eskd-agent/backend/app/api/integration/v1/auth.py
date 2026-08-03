from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.integration.deps import IntegrationPrincipal, get_integration_principal

router = APIRouter(prefix="/api/v1/auth", tags=["integration-auth"])


@router.get("/me")
async def auth_me(principal: IntegrationPrincipal = Depends(get_integration_principal)) -> dict:
    return {
        "subject": principal.subject,
        "roles": principal.roles,
        "auth_type": principal.auth_type,
    }
