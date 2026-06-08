from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.agent_builder import (
    AgentBlueprintRead,
    AgentBuilderAttemptRead,
    AgentBuilderMessageCreate,
    AgentBuilderPlanRead,
    AgentBuilderSessionCreate,
    AgentBuilderSessionDetailRead,
    AgentBuilderSessionRead,
    AgentBuilderToolCatalogItem,
)
from app.services.agent_builder_service import AgentBuilderService, AgentBuilderServiceError

router = APIRouter(prefix="/agent-builder", tags=["agent-builder"])


@router.post("/sessions", response_model=AgentBuilderSessionRead, status_code=status.HTTP_201_CREATED)
async def create_session(payload: AgentBuilderSessionCreate, db: DbSession, current_user: CurrentUser):
    try:
        session = await AgentBuilderService(db).create_session(payload.goal, current_user=current_user)
        await db.commit()
        return session
    except AgentBuilderServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/sessions", response_model=list[AgentBuilderSessionRead])
async def list_sessions(db: DbSession, current_user: CurrentUser):
    return await AgentBuilderService(db).list_sessions(current_user=current_user)


@router.get("/sessions/{session_id}", response_model=AgentBuilderSessionDetailRead)
async def get_session(session_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        return await AgentBuilderService(db).get_session_detail(session_id, current_user=current_user)
    except AgentBuilderServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        await AgentBuilderService(db).delete_session(session_id, current_user=current_user)
        await db.commit()
    except AgentBuilderServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/sessions/{session_id}/start", response_model=AgentBuilderSessionDetailRead)
async def start_session(session_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        detail = await AgentBuilderService(db).start_design(session_id, current_user=current_user)
        await db.commit()
        return detail
    except AgentBuilderServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/sessions/{session_id}/message", response_model=AgentBuilderSessionDetailRead)
async def send_message(
    session_id: uuid.UUID,
    payload: AgentBuilderMessageCreate,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        detail = await AgentBuilderService(db).send_message(session_id, payload.message, current_user=current_user)
        await db.commit()
        return detail
    except AgentBuilderServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/sessions/{session_id}/plan", response_model=AgentBuilderPlanRead | None)
async def get_plan(session_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        return await AgentBuilderService(db).get_plan(session_id, current_user=current_user)
    except AgentBuilderServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/sessions/{session_id}/attempts", response_model=list[AgentBuilderAttemptRead])
async def get_attempts(session_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        return await AgentBuilderService(db).get_attempts(session_id, current_user=current_user)
    except AgentBuilderServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/sessions/{session_id}/blueprint", response_model=AgentBlueprintRead | None)
async def get_blueprint(session_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        return await AgentBuilderService(db).get_blueprint(session_id, current_user=current_user)
    except AgentBuilderServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/sessions/{session_id}/approve-blueprint", response_model=AgentBlueprintRead)
async def approve_blueprint(session_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        blueprint = await AgentBuilderService(db).approve_blueprint(session_id, current_user=current_user)
        await db.commit()
        return blueprint
    except AgentBuilderServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/sessions/{session_id}/preview", response_model=AgentBuilderSessionDetailRead)
async def run_preview(session_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        detail = await AgentBuilderService(db).run_preview(session_id, current_user=current_user)
        await db.commit()
        return detail
    except AgentBuilderServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/sessions/{session_id}/regenerate", response_model=AgentBuilderSessionDetailRead)
async def regenerate(session_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        detail = await AgentBuilderService(db).regenerate(session_id, current_user=current_user)
        await db.commit()
        return detail
    except AgentBuilderServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/tools", response_model=list[AgentBuilderToolCatalogItem])
async def list_tools(db: DbSession, current_user: CurrentUser):
    _ = current_user
    return AgentBuilderService(db).list_tool_catalog()
