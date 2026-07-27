from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.app_notification import (
    AppNotificationAcceptRead,
    AppNotificationAcceptRequest,
    AppNotificationListRead,
    AppNotificationOpenRead,
    AppNotificationRead,
)
from app.services.app_notification_service import (
    AppNotificationService,
    AppNotificationServiceError,
)
from app.services.meeting_permission import can_access_meeting_agent

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def _require_meeting_agent(db: DbSession, user: CurrentUser) -> None:
    if not await can_access_meeting_agent(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к агенту совещаний",
        )


def _error(exc: AppNotificationServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("", response_model=AppNotificationListRead)
async def list_notifications(
    db: DbSession,
    current_user: CurrentUser,
) -> AppNotificationListRead:
    await _require_meeting_agent(db, current_user)
    return await AppNotificationService(db).list_for_user(current_user)


@router.post("/{notification_id}/read", response_model=AppNotificationRead)
async def mark_notification_read(
    notification_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> AppNotificationRead:
    await _require_meeting_agent(db, current_user)
    try:
        item = await AppNotificationService(db).mark_read(notification_id, current_user)
        await db.commit()
        return item
    except AppNotificationServiceError as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.post("/{notification_id}/open", response_model=AppNotificationOpenRead)
async def open_notification(
    notification_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> AppNotificationOpenRead:
    """Открыть уведомление: для РГ — Outlook + предложение серии."""
    await _require_meeting_agent(db, current_user)
    try:
        result = await AppNotificationService(db).open(notification_id, current_user)
        await db.commit()
        return result
    except AppNotificationServiceError as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.post("/{notification_id}/accept", response_model=AppNotificationAcceptRead)
async def accept_notification(
    notification_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    payload: AppNotificationAcceptRequest | None = None,
) -> AppNotificationAcceptRead:
    """Принять предложение: создать серию РГ в статусе created (без Outlook /plan)."""
    await _require_meeting_agent(db, current_user)
    try:
        result = await AppNotificationService(db).accept(
            notification_id,
            current_user,
            payload,
        )
        await db.commit()
        return result
    except AppNotificationServiceError as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.post("/{notification_id}/dismiss", response_model=AppNotificationRead)
async def dismiss_notification(
    notification_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> AppNotificationRead:
    await _require_meeting_agent(db, current_user)
    try:
        item = await AppNotificationService(db).dismiss(notification_id, current_user)
        await db.commit()
        return item
    except AppNotificationServiceError as exc:
        await db.rollback()
        raise _error(exc) from exc
