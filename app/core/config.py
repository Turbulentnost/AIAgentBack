from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore")

    PROJECT_NAME: str = "Корпоративная платформа ИИ-агентов"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: Literal["dev", "test", "ope", "prod"] = "dev"
    DEBUG: bool = True
    SECRET_KEY: str = "change_me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "platform"
    POSTGRES_PASSWORD: str = "platform"
    POSTGRES_DB: str = "ai_agents"

    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""

    QDRANT_HOST: str = "qdrant"
    QDRANT_PORT: int = 6333
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION: str = "knowledge_base"

    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "documents"
    MINIO_USER_FILES_BUCKET: str = "ai-user-files"
    MINIO_SECURE: bool = False

    LLM_GATEWAY_BASE_URL: str = "http://localhost:11434/v1"
    LLM_GATEWAY_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    LLM_DEFAULT_MODEL: str = "gpt-4.1"
    LLM_EMBEDDING_MODEL: str = "text-embedding-3-small"

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        return str(PostgresDsn.build(scheme="postgresql+asyncpg", username=self.POSTGRES_USER, password=self.POSTGRES_PASSWORD, host=self.POSTGRES_HOST, port=self.POSTGRES_PORT, path=self.POSTGRES_DB))

    @computed_field
    @property
    def DATABASE_URL_SYNC(self) -> str:
        return str(PostgresDsn.build(scheme="postgresql+psycopg", username=self.POSTGRES_USER, password=self.POSTGRES_PASSWORD, host=self.POSTGRES_HOST, port=self.POSTGRES_PORT, path=self.POSTGRES_DB))

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
