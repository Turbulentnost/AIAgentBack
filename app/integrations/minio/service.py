from __future__ import annotations

import io
from datetime import timedelta

from minio import Minio


class MinioObjectService:
    def __init__(self, client: Minio, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def upload(self, *, object_name: str, data: bytes, content_type: str) -> str:
        self.ensure_bucket()
        self.client.put_object(
            self.bucket,
            object_name,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        return object_name

    def presigned_get_url(self, object_name: str, expires_minutes: int = 20) -> str:
        return self.client.presigned_get_object(
            self.bucket,
            object_name,
            expires=timedelta(minutes=expires_minutes),
        )

    def delete(self, object_name: str) -> None:
        self.client.remove_object(self.bucket, object_name)
