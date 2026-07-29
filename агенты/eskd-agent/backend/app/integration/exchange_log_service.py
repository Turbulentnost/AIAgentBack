from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import IntegrationExchangeLog


class ExchangeLogService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def log(
        self,
        *,
        sender: str,
        receiver: str,
        operation: str,
        result: str,
        request_id: str | None = None,
        job_id: uuid.UUID | None = None,
        external_document_id: str | None = None,
        designation: str | None = None,
        revision: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        retry_attempt: int = 0,
        actor: str | None = None,
        payload_summary: dict | None = None,
    ) -> IntegrationExchangeLog:
        row = IntegrationExchangeLog(
            sender=sender,
            receiver=receiver,
            request_id=request_id,
            job_id=job_id,
            external_document_id=external_document_id,
            designation=designation,
            revision=revision,
            operation=operation,
            result=result,
            error_code=error_code,
            error_message=error_message,
            retry_attempt=retry_attempt,
            actor=actor,
            payload_summary=payload_summary,
            occurred_at=datetime.now(timezone.utc),
        )
        self._db.add(row)
        await self._db.commit()
        await self._db.refresh(row)
        return row

    async def list_logs(
        self,
        *,
        page: int = 1,
        size: int = 50,
        request_id: str | None = None,
        source_system: str | None = None,
    ) -> tuple[list[IntegrationExchangeLog], int]:
        query = select(IntegrationExchangeLog).order_by(IntegrationExchangeLog.occurred_at.desc())
        if request_id:
            query = query.where(IntegrationExchangeLog.request_id == request_id)
        if source_system:
            query = query.where(IntegrationExchangeLog.sender == source_system)
        count_query = select(func.count()).select_from(IntegrationExchangeLog)
        if request_id:
            count_query = count_query.where(IntegrationExchangeLog.request_id == request_id)
        if source_system:
            count_query = count_query.where(IntegrationExchangeLog.sender == source_system)
        total = int((await self._db.scalar(count_query)) or 0)
        rows = (await self._db.scalars(query.offset((page - 1) * size).limit(size))).all()
        return list(rows), total
