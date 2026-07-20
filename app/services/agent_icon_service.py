from __future__ import annotations

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.minio import MinioObjectError, MinioObjectService, get_minio_client
from app.models.agent import Agent
from app.services.profile_image_service import AvatarValidationError, ProfileImageService


class AgentIconService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.storage = MinioObjectService(get_minio_client(), settings.MINIO_USER_FILES_BUCKET)
        self._validator = ProfileImageService(db)

    async def upload_icon(self, agent: Agent, file: UploadFile) -> Agent:
        content = await file.read()
        content_type = file.content_type or "application/octet-stream"
        self._validator.validate_avatar(content, content_type)

        extension = self._validator._extension(content_type)
        object_name = f"agents/{agent.id}/icon{extension}"
        previous_object_name = agent.icon_object_name

        self.storage.upload(object_name=object_name, data=content, content_type=content_type)

        agent.icon_bucket = settings.MINIO_USER_FILES_BUCKET
        agent.icon_object_name = object_name
        await self.db.flush()

        if previous_object_name and previous_object_name != object_name:
            try:
                self.storage.delete(previous_object_name)
            except MinioObjectError:
                pass

        return agent

    def build_icon_url(self, agent: Agent, expires_minutes: int = 20) -> str | None:
        if not agent.icon_object_name:
            return None
        try:
            return self.storage.presigned_get_url(agent.icon_object_name, expires_minutes)
        except MinioObjectError:
            return None
