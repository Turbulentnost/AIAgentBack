"""Конфигурация агента. Все пороги и параметры из раздела 4 ТЗ — настраиваемые."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Режим работы
    use_stubs: bool = Field(default=True, alias="USE_STUBS")

    # Параметры обработки (раздел 4 ТЗ)
    imap_poll_interval_sec: int = Field(default=60, alias="IMAP_POLL_INTERVAL_SEC")
    spam_threshold: float = Field(default=0.85, alias="SPAM_THRESHOLD")
    spam_gray_zone_low: float = Field(default=0.70, alias="SPAM_GRAY_ZONE_LOW")
    dept_confidence_min: float = Field(default=0.70, alias="DEPT_CONFIDENCE_MIN")
    max_attachment_mb: int = Field(default=25, alias="MAX_ATTACHMENT_MB")

    mailboxes: str = Field(default="info@turbo-don.ru,pereadres@turbo-don.ru", alias="MAILBOXES")

    # Хранилища
    database_url: str = Field(
        default="postgresql+psycopg://agent:agent@localhost:5432/agent_pochta",
        alias="DATABASE_URL",
    )
    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    celery_broker_url: str = Field(
        default="amqp://guest:guest@localhost:5672//", alias="CELERY_BROKER_URL"
    )

    # Внешние сервисы платформы (при use_stubs=false)
    llm_gateway_url: str = Field(default="", alias="LLM_GATEWAY_URL")
    document_service_url: str = Field(default="", alias="DOCUMENT_SERVICE_URL")
    integration_service_url: str = Field(default="", alias="INTEGRATION_SERVICE_URL")
    vault_addr: str = Field(default="", alias="VAULT_ADDR")

    agent_version: str = Field(default="0.1.0", alias="AGENT_VERSION")

    @property
    def mailbox_list(self) -> list[str]:
        return [m.strip() for m in self.mailboxes.split(",") if m.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    """Singleton-доступ к настройкам."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
