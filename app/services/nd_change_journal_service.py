from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import NdChangeJournalEventType, NdChangeJournalSource
from app.models.nd_change_journal import NdChangeJournalEntry


class NdChangeJournalServiceError(Exception):
    pass


class NdChangeJournalService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def log_event(
        self,
        *,
        event_type: NdChangeJournalEventType,
        actor_user_id: uuid.UUID | None = None,
        resource_type: str,
        resource_id: str | uuid.UUID,
        summary: str,
        source: NdChangeJournalSource = NdChangeJournalSource.SYSTEM,
        department_id: uuid.UUID | None = None,
        template_id: uuid.UUID | None = None,
        document_id: uuid.UUID | None = None,
        document_code: str | None = None,
        document_name: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> NdChangeJournalEntry:
        entry = NdChangeJournalEntry(
            event_type=event_type,
            actor_user_id=actor_user_id,
            resource_type=resource_type,
            resource_id=str(resource_id),
            department_id=department_id,
            template_id=template_id,
            document_id=document_id,
            document_code=document_code,
            document_name=document_name,
            summary=summary,
            payload=payload,
            source=source,
        )
        self.db.add(entry)
        await self.db.flush()
        return entry

    async def list_entries(
        self,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        event_type: NdChangeJournalEventType | None = None,
        department_id: uuid.UUID | None = None,
        template_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        search: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[NdChangeJournalEntry], int]:
        stmt = select(NdChangeJournalEntry)
        if date_from is not None:
            stmt = stmt.where(NdChangeJournalEntry.created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(NdChangeJournalEntry.created_at <= date_to)
        if event_type is not None:
            stmt = stmt.where(NdChangeJournalEntry.event_type == event_type)
        if department_id is not None:
            stmt = stmt.where(NdChangeJournalEntry.department_id == department_id)
        if template_id is not None:
            stmt = stmt.where(NdChangeJournalEntry.template_id == template_id)
        if actor_id is not None:
            stmt = stmt.where(NdChangeJournalEntry.actor_user_id == actor_id)
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    NdChangeJournalEntry.summary.ilike(pattern),
                    NdChangeJournalEntry.document_code.ilike(pattern),
                    NdChangeJournalEntry.document_name.ilike(pattern),
                    NdChangeJournalEntry.resource_id.ilike(pattern),
                )
            )

        total = int(await self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        offset = max(0, (page - 1) * size)
        result = await self.db.execute(
            stmt.order_by(NdChangeJournalEntry.created_at.desc()).offset(offset).limit(size)
        )
        return list(result.scalars().all()), total

    async def get_entry_or_raise(self, entry_id: uuid.UUID) -> NdChangeJournalEntry:
        entry = await self.db.get(NdChangeJournalEntry, entry_id)
        if entry is None:
            raise NdChangeJournalServiceError("Запись журнала не найдена")
        return entry
