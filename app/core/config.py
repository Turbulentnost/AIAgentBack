from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    PROJECT_NAME: str = "Корпоративная платформа ИИ-агентов"
    APP_VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"
    DOCS_URL: str = "/docs"
    REDOC_URL: str = "/redoc"
    ENVIRONMENT: Literal["dev", "test", "ope", "prod"] = "dev"
    DEBUG: bool = True
    SQLALCHEMY_ECHO: bool = False
    SECRET_KEY: str = "change_me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    PASSWORD_MIN_LENGTH: int = 8
    BACKEND_CORS_ORIGINS: str = (
        "http://localhost:5173,http://127.0.0.1:5173,http://192.168.1.157:5173"
    )
    BACKEND_CORS_ALLOW_CREDENTIALS: bool = True
    BACKEND_CORS_ALLOW_METHODS: str = "*"
    BACKEND_CORS_ALLOW_HEADERS: str = "*"

    POSTGRES_HOST: str = "192.168.1.157"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "1234"
    POSTGRES_DB: str = "ai_agents"

    REDIS_HOST: str = "192.168.1.157"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""

    QDRANT_HOST: str = "192.168.1.157"
    QDRANT_PORT: int = 6333
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION: str = "knowledge_base"

    MINIO_ENDPOINT: str = "192.168.1.157:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "ai-documents"
    MINIO_USER_FILES_BUCKET: str = "ai-user-files"
    MINIO_SECURE: bool = False
    AVATAR_MAX_UPLOAD_SIZE_BYTES: int = 5 * 1024 * 1024
    AVATAR_ALLOWED_CONTENT_TYPES: str = "image/jpeg,image/png,image/webp"
    DOCUMENT_MAX_UPLOAD_SIZE_BYTES: int = 50 * 1024 * 1024
    DOCUMENT_ALLOWED_CONTENT_TYPES: str = (
        "application/pdf,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/msword,"
        "application/vnd.ms-excel,"
        "text/plain,"
        "text/csv,"
        "image/png,"
        "image/jpeg,"
        "image/webp"
    )

    LLM_GATEWAY_BASE_URL: str = "http://localhost:11434/v1"
    LLM_GATEWAY_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    LLM_DEFAULT_MODEL: str = "gpt-4.1"
    LLM_EMBEDDING_MODEL: str = "text-embedding-3-small"
    VISION_LM_STUDIO_BASE_URL: str = "http://172.18.0.1:1234/v1"
    VISION_LM_STUDIO_MODEL: str = "qwen/qwen3.5-9b"

    @property
    def cors_origins(self) -> list[str]:
        return self._parse_csv(self.BACKEND_CORS_ORIGINS)

    @property
    def cors_allow_methods(self) -> list[str]:
        return self._parse_csv(self.BACKEND_CORS_ALLOW_METHODS)

    @property
    def cors_allow_headers(self) -> list[str]:
        return self._parse_csv(self.BACKEND_CORS_ALLOW_HEADERS)

    @property
    def document_allowed_content_types(self) -> list[str]:
        return self._parse_csv(self.DOCUMENT_ALLOWED_CONTENT_TYPES)

    @property
    def avatar_allowed_content_types(self) -> list[str]:
        return self._parse_csv(self.AVATAR_ALLOWED_CONTENT_TYPES)

    def _parse_csv(self, value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.POSTGRES_USER,
                password=self.POSTGRES_PASSWORD,
                host=self.POSTGRES_HOST,
                port=self.POSTGRES_PORT,
                path=self.POSTGRES_DB,
            )
        )

    @computed_field
    @property
    def DATABASE_URL_SYNC(self) -> str:
        return str(
            PostgresDsn.build(
                scheme="postgresql+psycopg",
                username=self.POSTGRES_USER,
                password=self.POSTGRES_PASSWORD,
                host=self.POSTGRES_HOST,
                port=self.POSTGRES_PORT,
                path=self.POSTGRES_DB,
            )
        )

    @computed_field
    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @computed_field
    @property
    def QDRANT_URL(self) -> str:
        return f"http://{self.QDRANT_HOST}:{self.QDRANT_PORT}"

    def celery_broker(self) -> str:
        return self.CELERY_BROKER_URL or f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/1"

    def celery_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/2"


@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
