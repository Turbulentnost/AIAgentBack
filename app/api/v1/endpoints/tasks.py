from __future__ import annotations
import uuid
from fastapi import APIRouter, HTTPException, status
from app.api.deps import DbSession
from app.schemas.task import TaskCreate, TaskRead
from app.services.task_service import TaskService
router = APIRouter(prefix="/tasks", tags=["tasks"])
@router.get("", response_model=list[TaskRead])
async def list_tasks(db: DbSession, limit: int = 50, offset: int = 0):
    return await TaskService(db).list(limit, offset)
@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(db: DbSession, data: TaskCreate):
    return await TaskService(db).create(data)
@router.get("/{task_id}", response_model=TaskRead)
async def get_task(db: DbSession, task_id: uuid.UUID):
    task = await TaskService(db).get(task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
    return task
