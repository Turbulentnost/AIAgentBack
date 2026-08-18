from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

import urllib3
from minio import Minio

from app.core.config import settings


def _normalize_minio_endpoint(endpoint: str) -> str:
    value = endpoint.strip()
    if "://" in value:
        parsed = urlparse(value)
        return parsed.netloc or value.split("://", 1)[-1]
    return value


def _minio_http_client() -> urllib3.PoolManager:
    connect_timeout = max(1.0, float(getattr(settings, "MINIO_CONNECT_TIMEOUT", 3)))
    read_timeout = max(1.0, float(getattr(settings, "MINIO_READ_TIMEOUT", 10)))
    return urllib3.PoolManager(
        timeout=urllib3.Timeout(connect=connect_timeout, read=read_timeout),
        cert_reqs="CERT_NONE" if not settings.MINIO_SECURE else "CERT_REQUIRED",
    )


@lru_cache
def get_minio_client() -> Minio:
    return Minio(
        _normalize_minio_endpoint(settings.MINIO_ENDPOINT),
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE,
        http_client=_minio_http_client(),
    )


@lru_cache
def get_minio_presign_client() -> Minio:
    """Клиент для presigned URL — подпись должна совпадать с host, доступным браузеру."""
    endpoint = settings.minio_presign_endpoint
    return Minio(
        _normalize_minio_endpoint(endpoint),
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE,
        http_client=_minio_http_client(),
    )
