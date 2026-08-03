"""In-app уведомления (колокольчик) и реакция на РГ по проекту."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_notification import AppNotification
from app.models.enums import AppNotificationType
from app.models.user import User
from app.schemas.app_notification import (
    AppNotificationAcceptRead,
    AppNotificationAcceptRequest,
    AppNotificationListRead,
    AppNotificationOpenRead,
    AppNotificationRead,
    TurboProjectRgSeriesProposal,
)
from app.services.turbo_project_series_sync_service import (
    TurboProjectSeriesSyncError,
    TurboProjectSeriesSyncService,
)


class AppNotificationServiceError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class AppNotificationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_for_user(self, user: User) -> AppNotificationListRead:
        result = await self.db.execute(
            select(AppNotification)
            .where(AppNotification.user_id == user.id)
            .order_by(AppNotification.created_at.desc())
            .limit(100)
        )
        items = list(result.scalars().all())
        unread = await self.db.scalar(
            select(func.count())
            .select_from(AppNotification)
            .where(
                AppNotification.user_id == user.id,
                AppNotification.read_at.is_(None),
                AppNotification.resolved_at.is_(None),
            )
        )
        return AppNotificationListRead(
            items=[AppNotificationRead.model_validate(item) for item in items],
            unread_count=int(unread or 0),
        )

    async def mark_read(self, notification_id: uuid.UUID, user: User) -> AppNotificationRead:
        notification = await self._get_owned(notification_id, user)
        if notification.read_at is None:
            notification.read_at = datetime.now(timezone.utc)
            await self.db.flush()
        return AppNotificationRead.model_validate(notification)

    async def dismiss(self, notification_id: uuid.UUID, user: User) -> AppNotificationRead:
        notification = await self._get_owned(notification_id, user)
        now = datetime.now(timezone.utc)
        notification.read_at = notification.read_at or now
        notification.resolved_at = now
        await self.db.flush()
        return AppNotificationRead.model_validate(notification)

    async def open(
        self,
        notification_id: uuid.UUID,
        user: User,
    ) -> AppNotificationOpenRead:
        notification = await self._get_owned(notification_id, user)
        now = datetime.now(timezone.utc)
        notification.read_at = notification.read_at or now
        notification.opened_at = notification.opened_at or now
        await self.db.flush()

        proposal: TurboProjectRgSeriesProposal | None = None
        if notification.type == AppNotificationType.TURBO_PROJECT_RG.value:
            file_id = self._payload_file_id(notification)
            try:
                proposal = await TurboProjectSeriesSyncService(self.db).build_series_proposal(
                    file_id
                )
            except TurboProjectSeriesSyncError as exc:
                raise AppNotificationServiceError(
                    str(exc),
                    status_code=exc.status_code,
                ) from exc

        return AppNotificationOpenRead(
            notification=AppNotificationRead.model_validate(notification),
            proposal=proposal,
        )

    async def accept(
        self,
        notification_id: uuid.UUID,
        user: User,
        payload: AppNotificationAcceptRequest | None = None,
    ) -> AppNotificationAcceptRead:
        notification = await self._get_owned(notification_id, user)
        if notification.type != AppNotificationType.TURBO_PROJECT_RG.value:
            raise AppNotificationServiceError(
                "Это уведомление не связано с проектом TurboProject",
                status_code=422,
            )
        if notification.resolved_at is not None:
            raise AppNotificationServiceError(
                "Уведомление уже обработано",
                status_code=409,
            )

        file_id = self._payload_file_id(notification)
        sync = TurboProjectSeriesSyncService(self.db)
        try:
            proposal = await sync.build_series_proposal(file_id)
            meeting = await sync.create_series_from_proposal(
                proposal,
                weekday=payload.weekday if payload else None,
                time_local=payload.time_local if payload else None,
                duration_minutes=payload.duration_minutes if payload else None,
            )
        except TurboProjectSeriesSyncError as exc:
            raise AppNotificationServiceError(
                str(exc),
                status_code=exc.status_code,
            ) from exc

        now = datetime.now(timezone.utc)
        related = await self.db.execute(
            select(AppNotification).where(
                AppNotification.entity_key == notification.entity_key
            )
        )
        for item in related.scalars().all():
            item.read_at = item.read_at or now
            item.resolved_at = now
        await self.db.flush()
        await self.db.refresh(notification)

        return AppNotificationAcceptRead(
            notification=AppNotificationRead.model_validate(notification),
            scheduled_meeting=meeting,
        )

    async def _get_owned(
        self,
        notification_id: uuid.UUID,
        user: User,
    ) -> AppNotification:
        notification = await self.db.get(AppNotification, notification_id)
        if notification is None or notification.user_id != user.id:
            raise AppNotificationServiceError("Уведомление не найдено", status_code=404)
        return notification

    @staticmethod
    def _payload_file_id(notification: AppNotification) -> int:
        payload = notification.payload if isinstance(notification.payload, dict) else {}
        raw = payload.get("file_id")
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise AppNotificationServiceError(
                "В уведомлении нет корректного file_id проекта",
                status_code=422,
            ) from exc
