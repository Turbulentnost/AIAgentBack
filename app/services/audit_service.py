from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


class AuditService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def log(
        self,
        *,
        action: str,
        actor_id: uuid.UUID | None = None,
        actor_type: str = "user",
        resource_type: str | None = None,
        resource_id: str | None = None,
        payload: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        note: str | None = None,
    ) -> AuditLog:
        item = AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=payload,
            ip_address=ip_address,
            user_agent=user_agent,
            note=note,
        )
        self.db.add(item)
        await self.db.flush()
        return item
