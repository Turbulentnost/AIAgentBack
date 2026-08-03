from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import NdAcknowledgementStatus
from app.models.nd_acknowledgement import NdAcknowledgementAssignment
from app.models.user import User
from app.schemas.turbo_smk import NdAcknowledgementCreate


class NdAcknowledgementServiceError(Exception):
    pass


class NdAcknowledgementService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def assign(self, payload: NdAcknowledgementCreate) -> list[NdAcknowledgementAssignment]:
        if not payload.user_ids:
            raise NdAcknowledgementServiceError("Не указаны пользователи для ознакомления")
        items: list[NdAcknowledgementAssignment] = []
        for user_id in payload.user_ids:
            item = NdAcknowledgementAssignment(
                document_id=payload.document_id,
                document_version_id=payload.document_version_id,
                change_request_id=payload.change_request_id,
                user_id=user_id,
                due_at=payload.due_at,
                document_code=payload.document_code,
                document_name=payload.document_name,
                status=NdAcknowledgementStatus.PENDING,
            )
            self.db.add(item)
            items.append(item)
        await self.db.flush()
        return items

    async def list_for_user(
        self,
        user: User,
        *,
        status: NdAcknowledgementStatus | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[NdAcknowledgementAssignment], int]:
        query = select(NdAcknowledgementAssignment).where(NdAcknowledgementAssignment.user_id == user.id)
        if status is not None:
            query = query.where(NdAcknowledgementAssignment.status == status)
        total = await self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        result = await self.db.execute(
            query.order_by(NdAcknowledgementAssignment.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        return list(result.scalars().all()), int(total)

    async def confirm(
        self,
        assignment_id: uuid.UUID,
        *,
        user: User,
        note: str | None = None,
    ) -> NdAcknowledgementAssignment:
        item = await self.db.get(NdAcknowledgementAssignment, assignment_id)
        if item is None or item.user_id != user.id:
            raise NdAcknowledgementServiceError("Назначение ознакомления не найдено")
        item.status = NdAcknowledgementStatus.ACKNOWLEDGED
        item.acknowledged_at = datetime.now(UTC)
        item.note = note
        await self.db.flush()
        return item
