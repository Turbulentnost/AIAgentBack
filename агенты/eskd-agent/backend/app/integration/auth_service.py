from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import IntegrationApiKey


class AuthService:
    ROLE_ALL = {
        "ESKD_Administrators",
        "ESKD_NormControl",
        "ESKD_OTK",
        "ESKD_Designers",
        "ESKD_Managers",
        "ESKD_Auditors",
    }

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    @staticmethod
    def hash_key(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    async def verify_api_key(self, raw_key: str) -> IntegrationApiKey | None:
        key_hash = self.hash_key(raw_key.strip())
        row = await self._db.scalar(
            select(IntegrationApiKey).where(
                IntegrationApiKey.key_hash == key_hash,
                IntegrationApiKey.enabled.is_(True),
            )
        )
        if row:
            row.last_used_at = datetime.now(timezone.utc)
            await self._db.commit()
        return row

    async def create_api_key(self, *, name: str, roles: list[str]) -> tuple[IntegrationApiKey, str]:
        raw = secrets.token_urlsafe(32)
        row = IntegrationApiKey(
            name=name,
            key_hash=self.hash_key(raw),
            roles=roles or ["ESKD_Designers"],
        )
        self._db.add(row)
        await self._db.commit()
        await self._db.refresh(row)
        return row, raw

    @staticmethod
    def roles_from_ldap_groups(groups: list[str], mapping: dict[str, str]) -> list[str]:
        roles: list[str] = []
        for group, role in mapping.items():
            if group in groups:
                roles.append(role)
        return roles or ["ESKD_Designers"]
