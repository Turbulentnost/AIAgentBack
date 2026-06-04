from __future__ import annotations

import uuid

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.minio import MinioObjectService, get_minio_client
from app.models.user import User, UserProfileImage


class ProfileImageService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.storage = MinioObjectService(get_minio_client(), settings.MINIO_USER_FILES_BUCKET)

    async def upload_avatar(self, user: User, file: UploadFile) -> User:
        content = await file.read()
        content_type = file.content_type or "application/octet-stream"
        extension = self._extension(file.filename)
        object_name = f"users/{user.id}/profile/avatar_original{extension}"
        self.storage.upload(object_name=object_name, data=content, content_type=content_type)

        user.avatar_bucket = settings.MINIO_USER_FILES_BUCKET
        user.avatar_object_name = object_name
        self.db.add(
            UserProfileImage(
                user_id=user.id,
                bucket=settings.MINIO_USER_FILES_BUCKET,
                object_name=object_name,
                size="original",
                content_type=content_type,
                is_current=True,
            )
        )
        await self.db.flush()
        return user

    def build_avatar_url(self, user: User, expires_minutes: int = 20) -> str | None:
        if not user.avatar_object_name:
            return None
        return self.storage.presigned_get_url(user.avatar_object_name, expires_minutes)

    def _extension(self, filename: str | None) -> str:
        if not filename or "." not in filename:
            return ".bin"
        suffix = filename.rsplit(".", 1)[-1].lower()
        allowed = {"jpg", "jpeg", "png", "webp"}
        return f".{suffix}" if suffix in allowed else ".bin"
