from __future__ import annotations
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.task import Task
from app.schemas.task import TaskCreate
class TaskService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
    async def list(self, limit: int = 50, offset: int = 0) -> list[Task]:
        result = await self.db.execute(select(Task).order_by(Task.created_at.desc()).limit(limit).offset(offset))
        return list(result.scalars().all())
    async def get(self, task_id: uuid.UUID) -> Task | None:
        return await self.db.get(Task, task_id)
    async def create(self, data: TaskCreate, created_by_id: uuid.UUID | None = None) -> Task:
        task = Task(**data.model_dump(), created_by_id=created_by_id)
        self.db.add(task)
        await self.db.flush()
        return task
