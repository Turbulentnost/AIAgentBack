from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.integration.auth_service import AuthService


@dataclass
class IntegrationPrincipal:
    subject: str
    roles: list[str]
    auth_type: str


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "ESKD_Administrators": {"checks:read", "checks:write", "webhooks:write", "logs:read", "erp:read", "sed:write", "keys:write"},
    "ESKD_NormControl": {"checks:read", "checks:write", "logs:read", "sed:write"},
    "ESKD_OTK": {"checks:read", "checks:write", "logs:read"},
    "ESKD_Designers": {"checks:read", "checks:write"},
    "ESKD_Managers": {"checks:read", "erp:read", "logs:read"},
    "ESKD_Auditors": {"checks:read", "logs:read", "erp:read"},
}


def _permissions_for_roles(roles: list[str]) -> set[str]:
    perms: set[str] = set()
    for role in roles:
        perms.update(ROLE_PERMISSIONS.get(role, set()))
    return perms


async def get_integration_principal(
    db: AsyncSession = Depends(get_db),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
    x_dev_user: str | None = Header(default=None, alias="X-Dev-User"),
    x_dev_roles: str | None = Header(default=None, alias="X-Dev-Roles"),
) -> IntegrationPrincipal:
    if x_api_key or (authorization and authorization.lower().startswith("bearer ")):
        raw = x_api_key or authorization.split(" ", 1)[1].strip()
        key = await AuthService(db).verify_api_key(raw)
        if not key:
            raise HTTPException(401, "Invalid API key")
        return IntegrationPrincipal(subject=key.name, roles=list(key.roles or []), auth_type="api_key")

    if settings.auth_mode == "dev":
        roles = [r.strip() for r in (x_dev_roles or "").split(",") if r.strip()] or settings.dev_roles_list
        return IntegrationPrincipal(
            subject=x_dev_user or "dev-user",
            roles=roles,
            auth_type="dev",
        )

    if settings.integration_api_key_required:
        raise HTTPException(401, "API key required")

    return IntegrationPrincipal(
        subject=x_dev_user or "anonymous",
        roles=settings.dev_roles_list,
        auth_type="anonymous",
    )


def require_permission(permission: str):
    async def _guard(principal: IntegrationPrincipal = Depends(get_integration_principal)) -> IntegrationPrincipal:
        if permission not in _permissions_for_roles(principal.roles):
            raise HTTPException(403, f"Missing permission: {permission}")
        return principal

    return _guard
