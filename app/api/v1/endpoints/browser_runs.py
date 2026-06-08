from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.browser_run import BrowserRunCreate, BrowserRunRead, BrowserRunResult
from app.services.browser_runner_service import BrowserRunnerError, BrowserRunnerService

router = APIRouter(prefix="/browser-runs", tags=["browser-runs"])


@router.post("", response_model=BrowserRunRead, status_code=status.HTTP_201_CREATED)
async def create_browser_run(payload: BrowserRunCreate, db: DbSession, current_user: CurrentUser):
    try:
        run = await BrowserRunnerService(db).create_run(
            payload,
            requested_by_user_id=current_user.id,
            requested_by_agent_id=payload.agent_id,
            task_id=payload.task_id,
        )
        await db.commit()
        return run
    except BrowserRunnerError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/pending", response_model=list[BrowserRunRead])
async def list_pending_browser_runs(db: DbSession, current_user: CurrentUser):
    runs = await BrowserRunnerService(db).list_pending_for_user(current_user.id)
    await db.commit()
    return runs


@router.post("/{run_id}/result", response_model=BrowserRunRead)
async def submit_browser_run_result(
    run_id: uuid.UUID,
    payload: BrowserRunResult,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        run = await BrowserRunnerService(db).submit_result(run_id, current_user.id, payload)
        await db.commit()
        return run
    except BrowserRunnerError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{run_id}", response_model=BrowserRunRead)
async def get_browser_run(run_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    run = await BrowserRunnerService(db).get_run(run_id)
    if run is None or (run.requested_by_user_id != current_user.id and not current_user.is_superuser):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BrowserRun не найден")
    return run
