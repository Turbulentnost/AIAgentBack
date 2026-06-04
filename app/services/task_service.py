from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models.document import Document
from app.models.task import Task, TaskResult
from app.schemas.task import TaskCreate, TaskResultCreate

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

    async def get_current_result(self, task_id: uuid.UUID) -> TaskResult | None:
        result = await self.db.execute(
            select(TaskResult).where(TaskResult.task_id == task_id, TaskResult.is_current.is_(True))
        )
        return result.scalar_one_or_none()

    async def save_result(self, task: Task, data: TaskResultCreate) -> TaskResult:
        existing = await self.db.execute(
            select(TaskResult).where(TaskResult.task_id == task.id, TaskResult.is_current.is_(True))
        )
        for result in existing.scalars().all():
            result.is_current = False

        task_result = TaskResult(
            task_id=task.id,
            agent_id=data.agent_id or task.agent_id,
            status=data.status,
            conclusion=data.conclusion,
            summary=data.summary,
            findings=data.findings,
            data_confidence=data.data_confidence,
            requires_human_review=data.requires_human_review,
            additional_data=data.additional_data,
            report_bucket=data.report_bucket,
            report_object_name=data.report_object_name,
            report_url=data.report_url,
            is_current=True,
            generated_at=datetime.now(timezone.utc),
            metadata_=data.metadata,
            raw_output=data.raw_output,
        )
        self.db.add(task_result)
        await self.db.flush()
        return task_result
