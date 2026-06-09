from __future__ import annotations
import io
from minio import Minio
from app.core.config import settings


class ObjectStorageError(RuntimeError):
    pass


class ObjectStorage:
    def __init__(self) -> None:
        self._client: Minio | None = None
        self.bucket = settings.MINIO_BUCKET

    @property
    def client(self) -> Minio:
        if self._client is None:
            self._client = Minio(settings.MINIO_ENDPOINT, access_key=settings.MINIO_ACCESS_KEY, secret_key=settings.MINIO_SECRET_KEY, secure=settings.MINIO_SECURE)
        return self._client

    def ensure_bucket(self) -> None:
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
        except Exception as exc:
            raise ObjectStorageError("Не удалось проверить или создать bucket в MinIO") from exc

    def put_object(self, key: str, data: bytes, content_type: str) -> str:
        try:
            self.ensure_bucket()
            self.client.put_object(self.bucket, key, io.BytesIO(data), length=len(data), content_type=content_type)
            return key
        except ObjectStorageError:
            raise
        except Exception as exc:
            raise ObjectStorageError("Не удалось загрузить файл в MinIO") from exc

    def put_object_to_bucket(self, bucket: str, key: str, data: bytes, content_type: str) -> str:
        try:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)
            self.client.put_object(bucket, key, io.BytesIO(data), length=len(data), content_type=content_type)
            return key
        except Exception as exc:
            raise ObjectStorageError("Не удалось загрузить файл в MinIO") from exc

    def get_object(self, key: str) -> bytes:
        try:
            response = self.client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except Exception as exc:
            raise ObjectStorageError("Не удалось скачать файл из MinIO") from exc

    def get_object_from_bucket(self, bucket: str, key: str) -> bytes:
        try:
            response = self.client.get_object(bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except Exception as exc:
            raise ObjectStorageError("Не удалось скачать файл из MinIO") from exc

    def delete_object(self, key: str) -> None:
        try:
            self.client.remove_object(self.bucket, key)
        except Exception as exc:
            raise ObjectStorageError("Не удалось удалить файл из MinIO") from exc


object_storage = ObjectStorage()
