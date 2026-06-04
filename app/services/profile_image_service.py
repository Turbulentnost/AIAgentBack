from __future__ import annotations

import uuid

from fastapi import UploadFile
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.minio import MinioObjectError, MinioObjectService, get_minio_client
from app.models.user import User, UserProfileImage


class AvatarValidationError(ValueError):
    pass


class AvatarMetadataSaveError(RuntimeError):
    pass


class ProfileImageService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.storage = MinioObjectService(get_minio_client(), settings.MINIO_USER_FILES_BUCKET)

    async def upload_avatar(self, user: User, file: UploadFile) -> User:
        content = await file.read()
        content_type = file.content_type or "application/octet-stream"
        self.validate_avatar(content, content_type)

        extension = self._extension(content_type)
        object_name = f"users/{user.id}/profile/avatar{extension}"
        self.storage.upload(object_name=object_name, data=content, content_type=content_type)

        try:
            await self.db.execute(
                update(UserProfileImage)
                .where(UserProfileImage.user_id == user.id)
                .values(is_current=False)
            )
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
        except Exception as exc:
            await self.db.rollback()
            try:
                self.storage.delete(object_name)
            except MinioObjectError as cleanup_exc:
                raise AvatarMetadataSaveError(
                    "Аватар загружен в MinIO, но metadata не сохранилась в PostgreSQL. "
                    "Автоматически удалить объект не удалось; нужна ручная очистка."
                ) from cleanup_exc
            raise AvatarMetadataSaveError(
                "Аватар загружен в MinIO, но metadata не сохранилась в PostgreSQL. "
                "Загруженный объект удалён из MinIO."
            ) from exc
        return user

    def build_avatar_url(self, user: User, expires_minutes: int = 20) -> str | None:
        if not user.avatar_object_name:
            return None
        try:
            return self.storage.presigned_get_url(user.avatar_object_name, expires_minutes)
        except MinioObjectError:
            return None

    def validate_avatar(self, content: bytes, content_type: str) -> None:
        if not content:
            raise AvatarValidationError("Файл аватара пустой")
        if len(content) > settings.AVATAR_MAX_UPLOAD_SIZE_BYTES:
            raise AvatarValidationError(
                f"Аватар превышает максимальный размер {settings.AVATAR_MAX_UPLOAD_SIZE_BYTES} байт"
            )
        if content_type not in settings.avatar_allowed_content_types:
            raise AvatarValidationError(f"Формат аватара не поддерживается: {content_type}")

    def _extension(self, content_type: str) -> str:
        extensions = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        return extensions.get(content_type, ".bin")
