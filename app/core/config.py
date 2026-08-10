from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../infrastructure/.env", ".env"),
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
    # Авион (Excel): если false — classify/analyze/templates без обязательной сессии платформы.
    # Нужно, когда мобилка логинится на один host, а Aveon API — на другой (разная БД jti).
    DOCUMENT_ANALYSIS_REQUIRE_AUTH: bool = True
    # Принимать JWT по подписи без строки UserSession (общий SECRET_KEY, разные БД сессий).
    AUTH_ALLOW_JWT_WITHOUT_SESSION: bool = False
    ONEC_AUTH_API_BASE_URL: str = "http://192.168.0.247:8000/api/v1"
    ONEC_TOKEN_MAX_AGE_HOURS: int = 4
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
    REDIS_PORT: int = 16379
    REDIS_DB: int = 0
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""
    CELERY_VISIBILITY_TIMEOUT_SECONDS: int = 60 * 60 * 24
    KB_INDEXING_STALE_AFTER_SECONDS: int = 60 * 45
    KB_INDEXING_RECOVERY_INTERVAL_SECONDS: int = 60 * 10
    KB_INDEXING_RECOVERY_MAX_JOBS: int = 5
    KB_DOCUMENT_PARSE_TIMEOUT_SECONDS: int = 60 * 10

    QDRANT_HOST: str = "192.168.1.157"
    QDRANT_PORT: int = 6333
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION: str = "knowledge_base_bge_m3"
    QDRANT_VECTOR_SIZE: int = 1024

    # Режим поиска по базе знаний: "semantic" — чистый векторный (смысловой)
    # поиск; "hybrid" — вектор + полнотекстовый поиск по ключевым словам.
    # Гибрид устойчивее на коротких запросах («База данных»): смысловой поиск
    # дополняется точными совпадениями слов, итог объединяется через RRF.
    KB_SEARCH_MODE: str = "hybrid"
    # Минимальная косинусная близость, ниже которой фрагмент не показывается
    # (0 — без отсечки). Помогает не выдавать нерелевантные фрагменты.
    KB_SEARCH_MIN_SCORE: float = 0.0
    # Максимальное время (сек) на формирование «Предварительного ответа» через
    # LLM. По истечении показываем самый релевантный фрагмент без синтеза.
    KB_SEARCH_ANSWER_TIMEOUT: float = 45.0
    # Модели LM Studio для генерации ответа по фрагментам (через запятую).
    # Перебираются по порядку: если модель недоступна или вернула пустой
    # ответ — пробуем следующую.
    KB_SEARCH_ANSWER_MODELS: str = (
        "openai/gpt-oss-120b,qwen/qwen3.5-9b,yandexgpt-5-lite-8b-instruct"
    )

    MINIO_ENDPOINT: str = "192.168.1.157:9000"
    # Адрес MinIO для presigned URL в браузере (LAN/IP). В Docker backend использует minio:9000,
    # а клиенту отдаём публичный host:port, иначе <img src> не загрузится.
    MINIO_PUBLIC_ENDPOINT: str = ""
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

    # Общий LLM-шлюз (прочие задачи платформы).
    LLM_GATEWAY_BASE_URL: str = ""
    LLM_GATEWAY_API_KEY: str | None = None
    CLAUDE_API_KEY: str | None = None
    OPENAI_API_KEY_CLAUDE: str | None = None
    OPENAI_API_KEY: str | None = None
    LLM_DEFAULT_MODEL: str = ""
    LLM_EMBEDDING_MODEL: str = ""
    # Конструктор агентов: Claude → fallback в LM Studio (отдельно от OCR).
    AGENT_BUILDER_CLAUDE_MODEL: str = "claude-sonnet-4-20250514"
    AGENT_BUILDER_FALLBACK_BASE_URL: str = ""
    AGENT_BUILDER_FALLBACK_MODEL: str = "openai/gpt-oss-120b"
    # Procurement agent: Anthropic-compatible Claude endpoint.
    PROCUREMENT_LLM_BASE_URL: str = "https://api.claudehub.fun"
    PROCUREMENT_LLM_MODEL: str = "claude-sonnet-4-6"
    PROCUREMENT_LLM_MODELS: str = "claude-sonnet-4-6,claude-opus-4-1"
    PROCUREMENT_LLM_STYLE: str = "anthropic"
    PROCUREMENT_LLM_SUPPORTS_REASONING: bool = True
    PROCUREMENT_LLM_DISCOVER_MODELS: bool = True
    # Procurement orchestrator Level 0 polling.
    PROCUREMENT_ORCHESTRATOR_ENABLED: bool = True
    PROCUREMENT_ORCHESTRATOR_PLANNING_ENABLED: bool = False
    PROCUREMENT_ORCHESTRATOR_INTERVAL_SECONDS: int = 1800
    PROCUREMENT_ORCHESTRATOR_LOOKBACK_DAYS: int = 14
    PROCUREMENT_ORCHESTRATOR_PAGE_LIMIT: int = 50
    PROCUREMENT_ORCHESTRATOR_OVERLAP_HOURS: int = 6
    PROCUREMENT_ORCHESTRATOR_LOCK_TTL_SECONDS: int = 1700
    # Структурное извлечение nd_control (DocumentCard, анализ отдела).
    ND_CONTROL_EXTRACTION_MODEL: str = "openai/gpt-oss-120b"
    ND_CONTROL_EXTRACTION_LLM_TIMEOUT_SECONDS: int = 1200
    ND_TEMPLATE_CLASSIFICATION_MODEL: str | None = None
    ND_CONTROL_UML_MODEL: str | None = None
    ND_CONTROL_UML_LLM_TIMEOUT_SECONDS: int = 180
    # OCR / vision для PDF и изображений — всегда qwen в LM Studio.
    VISION_LM_STUDIO_BASE_URL: str = ""
    VISION_LM_STUDIO_MODEL: str = "qwen/qwen3.5-9b"
    VISION_OCR_TIMEOUT_SECONDS: int = 600
    # Агент закупок (Авион): qwen/qwen3.5-9b лучше подходит из доступных
    # локальных моделей для структурного анализа Excel и JSON-вывода.
    AVEON_LM_STUDIO_BASE_URL: str = "http://127.0.0.1:1234/v1"
    AVEON_LM_STUDIO_MODEL: str = "qwen/qwen3.5-9b"
    AVEON_LM_STUDIO_TIMEOUT_SECONDS: int = 600
    EMBEDDINGS_PROVIDER: str = "local"
    EMBEDDINGS_MODEL: str = "BAAI/bge-m3"
    EMBEDDINGS_VECTOR_SIZE: int = 1024
    EMBEDDINGS_DEVICE: str = "cuda"
    EMBEDDINGS_BATCH_SIZE: int = 16
    EMBEDDINGS_TIMEOUT_SECONDS: int = 60
    EMBEDDINGS_ALLOW_CPU_FALLBACK: bool = True
    EMBEDDINGS_MAX_TEXT_LENGTH: int = 20000
    BROWSER_ALLOWED_DOMAINS: str = (
        "1c.company.local,docs.company.local,portal.company.local,edo.company.local,"
        "wttr.in,pogoda.yandex.ru,yandex.ru,gismeteo.ru,www.gismeteo.ru,meteoinfo.ru,"
        "duckduckgo.com,html.duckduckgo.com,www.duckduckgo.com"
    )
    BROWSER_BLOCKED_SCHEMES: str = "file:,javascript:,data:"
    BROWSER_MAX_TIMEOUT_SECONDS: int = 60
    BROWSER_POLL_INTERVAL_SECONDS: float = 1.0
    # Runtime Sandbox: разрешить открытый веб (поиск + произвольные сайты), минуя allowlist.
    # Внутренние/loopback/private IP, localhost, исполняемые файлы и запрещённые схемы блокируются всегда.
    # Задеплоенные production-агенты остаются на ограничительном allowlist (allow_open_web=False).
    BROWSER_SANDBOX_OPEN_WEB: bool = True

    # 1С: ERP OData — значения только из .env (см. .env.example).
    ONEC_ODATA_URL: str = ""
    ONEC_ODATA_USER: str = ""
    ONEC_ODATA_PASSWORD: str = ""
    ONEC_ODATA_TIMEOUT: int = 120
    ONEC_MEETING_MEMO_THEME: str = ""
    ONEC_CORPORATE_EMAIL_DOMAIN: str = "turbo-don.ru"
    ONEC_NOTIFICATION_DEFAULT_RECIPIENT_FIOS: str = ""
    ONEC_NOTIFICATION_SOURCE_USER_FIO: str = ""
    MEETING_DASHBOARD_CACHE_ENABLED: bool = True
    MEETING_DASHBOARD_CACHE_TTL_SECONDS: int = 60 * 60 * 24
    MEETING_DASHBOARD_CACHE_WARMUP_ENABLED: bool = True
    MEETING_DASHBOARD_CACHE_WARMUP_HOURS: str = "10,15"
    MEETING_DASHBOARD_CACHE_WARMUP_MINUTE: int = 0
    MEETING_DASHBOARD_CACHE_WARMUP_TIMEZONE: str = "Europe/Moscow"

    # Фоновая синхронизация остатков и ресурсных спецификаций Aveon из 1С OData.
    # Каждые ONEC_SYNC_EVERY_HOURS часов в :ONEC_SYNC_MINUTE (Europe/Moscow), включая 00:00.
    ONEC_DAILY_SYNC_ENABLED: bool = True
    ONEC_SYNC_EVERY_HOURS: int = 1
    ONEC_SYNC_MINUTE: int = 0
    ONEC_DAILY_SYNC_LOCK_TTL_SECONDS: int = 60 * 55
    # Fallback: периодическая синхронизация внутри uvicorn, если Celery Beat не запущен.
    ONEC_INPROCESS_SYNC_ENABLED: bool = True

    # Google Sheets (Авион): Service Account для чтения закрытых таблиц.
    GOOGLE_SHEETS_SPREADSHEET_ID: str = "1C3SsvZ8IcK68d-nVCwOCnb7lmIy7SA03t6_wmHoJDqA"
    GOOGLE_SHEETS_SHEET_GID: str = "295357731"
    GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE: str = ""
    # Альтернатива файлу: JSON Service Account одной строкой (удобно для Docker secrets).
    GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON: str = ""

    # Outlook / Exchange (COM-календарь, EWS, SMTP) — значения из .env.
    OUTLOOK_EMAIL: str = ""
    OUTLOOK_PASSWORD: str = ""
    OUTLOOK_SERVER: str = ""
    OUTLOOK_MAILBOX: str = ""
    OUTLOOK_TIMEZONE: str = "Europe/Moscow"
    OUTLOOK_SMTP_HOST: str = ""
    OUTLOOK_SMTP_PORT: int = 587
    OUTLOOK_SMTP_TLS: str = "true"
    OUTLOOK_SMTP_FROM: str = ""
    # Адрес получателя обратной связи по Авиону (developer feedback).
    FEEDBACK_RECIPIENT_EMAIL: str = "sktb_razvitie5@turbo-don.ru"
    # Временный получатель отчётов о завершении смены менеджера.
    SHIFT_REPORT_RECIPIENT_EMAIL: str = "sktb_razvitie5@turbo-don.ru"
    SHIFT_COMPLETION_RECIPIENT_EMAIL: str = "sktb_razvitie5@turbo-don.ru"

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

    @property
    def browser_allowed_domains(self) -> list[str]:
        return self._parse_csv(self.BROWSER_ALLOWED_DOMAINS)

    @property
    def browser_blocked_schemes(self) -> list[str]:
        return self._parse_csv(self.BROWSER_BLOCKED_SCHEMES)

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
    def minio_presign_endpoint(self) -> str:
        """Host:port для presigned URL в браузере."""
        public = self.MINIO_PUBLIC_ENDPOINT.strip()
        if public:
            return public
        internal_host = self.MINIO_ENDPOINT.split(":", 1)[0].strip()
        if internal_host in {"minio", "localhost", "127.0.0.1"}:
            # Backend в Docker: MinIO снаружи на POSTGRES_HOST:9000 (проброс порта compose).
            return f"{self.POSTGRES_HOST}:9000"
        return self.MINIO_ENDPOINT

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
