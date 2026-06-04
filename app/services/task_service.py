from __future__ import annotations
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models.document import Document
from app.models.task import Task
from app.schemas.task import TaskCreate

class TaskService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
    async def list(self, limit: int = 50, offset: int = 0) -> list[Task]:
        result = await self.db.execute(
            select(Task)
            .options(selectinload(Task.documents), selectinload(Task.steps))
            .order_by(Task.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())
    async def get(self, task_id: uuid.UUID) -> Task | None:
        result = await self.db.execute(
            select(Task)
            .options(selectinload(Task.documents), selectinload(Task.steps))
            .where(Task.id == task_id)
        )
        return result.scalar_one_or_none()

    async def create(self, data: TaskCreate, created_by_id: uuid.UUID | None = None) -> Task:
        values = data.model_dump(exclude={"document_ids"})
        task = Task(**values, created_by_id=created_by_id)
        if data.document_ids:
            result = await self.db.execute(select(Document).where(Document.id.in_(data.document_ids)))
            task.documents = list(result.scalars().all())
        self.db.add(task)
        await self.db.flush()
        return task
