from __future__ import annotations

import io
from datetime import timedelta

from minio import Minio


class MinioObjectError(RuntimeError):
    pass


class MinioObjectService:
    def __init__(self, client: Minio, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def ensure_bucket(self) -> None:
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
        except Exception as exc:
            raise MinioObjectError("Не удалось проверить или создать bucket в MinIO") from exc

    def upload(self, *, object_name: str, data: bytes, content_type: str) -> str:
        try:
            self.ensure_bucket()
            self.client.put_object(
                self.bucket,
                object_name,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
            return object_name
        except MinioObjectError:
            raise
        except Exception as exc:
            raise MinioObjectError("Не удалось загрузить объект в MinIO") from exc

    def presigned_get_url(self, object_name: str, expires_minutes: int = 20) -> str:
        try:
            return self.client.presigned_get_object(
                self.bucket,
                object_name,
                expires=timedelta(minutes=expires_minutes),
            )
        except Exception as exc:
            raise MinioObjectError("Не удалось сформировать временную ссылку MinIO") from exc

    def delete(self, object_name: str) -> None:
        try:
            self.client.remove_object(self.bucket, object_name)
        except Exception as exc:
            raise MinioObjectError("Не удалось удалить объект из MinIO") from exc
