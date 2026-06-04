from __future__ import annotations

import uuid

from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.schemas.task import (
    CeleryDebugRequest,
    CeleryTaskEnqueueResponse,
    CeleryTaskStatusResponse,
    TaskCreate,
    TaskRead,
)
from app.services.task_service import TaskService
from app.workers.celery_app import celery_app
from app.workers.tasks import debug_task

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskRead])
async def list_tasks(db: DbSession, limit: int = 50, offset: int = 0):
    return await TaskService(db).list(limit, offset)


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(db: DbSession, data: TaskCreate):
    return await TaskService(db).create(data)


@router.post("/debug-celery", response_model=CeleryTaskEnqueueResponse)
async def enqueue_debug_task(data: CeleryDebugRequest) -> CeleryTaskEnqueueResponse:
    async_result = debug_task.apply_async(
        kwargs={"payload": {"message": data.message, **(data.payload or {})}},
        queue="default",
    )
    return CeleryTaskEnqueueResponse(
        celery_task_id=async_result.id,
        status="queued",
        queue="default",
    )


@router.get("/celery/{celery_task_id}", response_model=CeleryTaskStatusResponse)
async def get_celery_task_status(celery_task_id: str) -> CeleryTaskStatusResponse:
    result = AsyncResult(celery_task_id, app=celery_app)
    response = CeleryTaskStatusResponse(
        celery_task_id=celery_task_id,
        state=result.state,
        ready=result.ready(),
    )
    if result.ready():
        response.successful = result.successful()
        if result.successful():
            response.result = result.result
        else:
            response.error = str(result.result)
    return response


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(db: DbSession, task_id: uuid.UUID):
    task = await TaskService(db).get(task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
    return task
