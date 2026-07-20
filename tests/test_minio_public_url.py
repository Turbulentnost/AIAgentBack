from __future__ import annotations

from app.core.config import settings


def test_minio_presign_endpoint_uses_public_when_set(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setattr(settings, "MINIO_PUBLIC_ENDPOINT", "192.168.1.157:9000")
    monkeypatch.setattr(settings, "POSTGRES_HOST", "192.168.1.157")

    assert settings.minio_presign_endpoint == "192.168.1.157:9000"


def test_minio_presign_endpoint_falls_back_to_postgres_host_in_docker(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setattr(settings, "MINIO_PUBLIC_ENDPOINT", "")
    monkeypatch.setattr(settings, "POSTGRES_HOST", "192.168.1.157")

    assert settings.minio_presign_endpoint == "192.168.1.157:9000"


def test_minio_presign_endpoint_uses_internal_when_local(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MINIO_ENDPOINT", "192.168.1.157:9000")
    monkeypatch.setattr(settings, "MINIO_PUBLIC_ENDPOINT", "")
    monkeypatch.setattr(settings, "POSTGRES_HOST", "192.168.1.157")

    assert settings.minio_presign_endpoint == "192.168.1.157:9000"


def test_presigned_url_uses_public_host(monkeypatch) -> None:
    from unittest.mock import MagicMock

    import app.integrations.minio.service as minio_service_module

    monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setattr(settings, "MINIO_PUBLIC_ENDPOINT", "192.168.1.157:9000")
    monkeypatch.setattr(settings, "MINIO_SECURE", False)

    mock_presign = MagicMock(
        return_value="http://192.168.1.157:9000/ai-user-files/agents/x/icon.png?sig=1"
    )
    mock_client = MagicMock()
    mock_client.presigned_get_object = mock_presign
    monkeypatch.setattr(minio_service_module, "get_minio_presign_client", lambda: mock_client)

    svc = minio_service_module.MinioObjectService(MagicMock(), "ai-user-files")
    url = svc.presigned_get_url("agents/x/icon.png")

    assert url.startswith("http://192.168.1.157:9000/")
    mock_presign.assert_called_once()
