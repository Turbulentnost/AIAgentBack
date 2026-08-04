"""Конфигурация агента. Все пороги и параметры из раздела 4 ТЗ — настраиваемые."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень репозитория (…/agent-pochta), не зависит от cwd процесса Celery
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE) if ENV_FILE.is_file() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Режим работы
    use_stubs: bool = Field(default=True, alias="USE_STUBS")
    # dry_run | review | live (ТЗ §6)
    agent_mode: str = Field(default="live", alias="AGENT_MODE")
    routing_rules_path: str = Field(default="", alias="ROUTING_RULES_PATH")
    dialog_rules_path: str = Field(default="", alias="DIALOG_RULES_PATH")
    routing_corrections_path: str = Field(default="", alias="ROUTING_CORRECTIONS_PATH")
    spam_learning_path: str = Field(default="", alias="SPAM_LEARNING_PATH")

    # Параметры обработки (раздел 4 ТЗ)
    imap_poll_interval_sec: int = Field(default=10, alias="IMAP_POLL_INTERVAL_SEC")
    spam_threshold: float = Field(default=0.85, alias="SPAM_THRESHOLD")
    spam_gray_zone_low: float = Field(default=0.70, alias="SPAM_GRAY_ZONE_LOW")
    trusted_sender_domains: str = Field(default="", alias="TRUSTED_SENDER_DOMAINS")
    spam_skip_llm_for_trusted: bool = Field(default=True, alias="SPAM_SKIP_LLM_FOR_TRUSTED")
    dept_confidence_min: float = Field(default=0.70, alias="DEPT_CONFIDENCE_MIN")
    dept_confidence_chairman_min: float = Field(
        default=0.98, alias="DEPT_CONFIDENCE_CHAIRMAN_MIN"
    )
    dept_confidence_od_min: float = Field(default=0.95, alias="DEPT_CONFIDENCE_OD_MIN")
    dept_confidence_ved_min: float = Field(default=0.90, alias="DEPT_CONFIDENCE_VED_MIN")
    max_attachment_mb: int = Field(default=25, alias="MAX_ATTACHMENT_MB")
    # Таймаут IMAP при on-demand скачивании вложений (полный RFC822 может быть большим).
    imap_download_timeout_sec: int = Field(default=120, alias="IMAP_DOWNLOAD_TIMEOUT_SEC")
    document_extract_max_chars: int = Field(default=12_000, alias="DOCUMENT_EXTRACT_MAX_CHARS")
    document_extract_total_max_chars: int = Field(
        default=40_000, alias="DOCUMENT_EXTRACT_TOTAL_MAX_CHARS"
    )
    summary_body_max_chars: int = Field(default=12_000, alias="SUMMARY_BODY_MAX_CHARS")
    summary_attachments_max_chars: int = Field(
        default=20_000, alias="SUMMARY_ATTACHMENTS_MAX_CHARS"
    )
    document_storage_excerpt_chars: int = Field(
        default=2_000, alias="DOCUMENT_STORAGE_EXCERPT_CHARS"
    )
    summary_max_sentences: int = Field(default=5, alias="SUMMARY_MAX_SENTENCES")
    summary_max_chars: int = Field(default=800, alias="SUMMARY_MAX_CHARS")

    rag_backend: str = Field(default="stub", alias="RAG_BACKEND")  # stub | qdrant
    rag_department_enabled: bool = Field(default=True, alias="RAG_DEPARTMENT_ENABLED")

    mailboxes: str = Field(default="info@turbo-don.ru,pereadres@turbo-don.ru", alias="MAILBOXES")

    # IMAP (раздел 5.1)
    imap_host: str = Field(default="imap.yandex.ru", alias="IMAP_HOST")
    imap_port: int = Field(default=993, alias="IMAP_PORT")
    imap_username: str = Field(default="", alias="IMAP_USERNAME")
    imap_password: str = Field(default="", alias="IMAP_PASSWORD")
    imap_connect_timeout_sec: int = Field(default=30, alias="IMAP_CONNECT_TIMEOUT_SEC")
    imap_max_connect_retries: int = Field(default=3, alias="IMAP_MAX_CONNECT_RETRIES")
    imap_connect_retry_delay_sec: int = Field(default=300, alias="IMAP_CONNECT_RETRY_DELAY_SEC")
    imap_catchup_days: int = Field(default=7, alias="IMAP_CATCHUP_DAYS")
    imap_fetch_batch_size: int = Field(default=20, alias="IMAP_FETCH_BATCH_SIZE")
    imap_catchup_max_uids: int = Field(default=50, alias="IMAP_CATCHUP_MAX_UIDS")
    attachment_cache_ttl_sec: int = Field(default=1800, alias="ATTACHMENT_CACHE_TTL_SEC")
    attachment_cache_max_mb: int = Field(default=256, alias="ATTACHMENT_CACHE_MAX_MB")
    attachment_imap_partial_fetch: bool = Field(default=True, alias="ATTACHMENT_IMAP_PARTIAL_FETCH")

    # Повторы 1С (раздел 5.2)
    erp_retry_max: int = Field(default=5, alias="ERP_RETRY_MAX")
    erp_retry_delay_sec: int = Field(default=600, alias="ERP_RETRY_DELAY_SEC")

    # API агента
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8080, alias="API_PORT")
    database_url: str = Field(
        default="postgresql+psycopg://agent:agent@localhost:5433/agent_pochta",
        alias="DATABASE_URL",
    )
    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")

    # OData 1С → синхронизация RAG (scripts/sync_rag_from_1c.py)
    odata_base_url: str = Field(default="", alias="ODATA_BASE_URL")
    odata_username: str = Field(default="", alias="ODATA_USERNAME")
    odata_password: str = Field(default="", alias="ODATA_PASSWORD")
    odata_contractors_entity: str = Field(default="", alias="ODATA_CONTRACTORS_ENTITY")
    odata_departments_entity: str = Field(default="", alias="ODATA_DEPARTMENTS_ENTITY")
    # Запись документа «Входящая корреспонденция» через OData (узел 7)
    erp_mode: str = Field(default="", alias="ERP_MODE")  # odata | http | stub
    use_odata_erp: bool = Field(default=False, alias="USE_ODATA_ERP")
    odata_incoming_doc_entity: str = Field(
        default="Document_ТД_ВходящаяКорреспонденция",
        alias="ODATA_INCOMING_DOC_ENTITY",
    )
    odata_business_process_entities: str = Field(
        default="BusinessProcess_Задание,BusinessProcess_CRM_БизнесПроцесс",
        alias="ODATA_BUSINESS_PROCESS_ENTITIES",
    )
    odata_incoming_field_map: str = Field(default="", alias="ODATA_INCOMING_FIELD_MAP")
    odata_incoming_extra_fields: str = Field(default="", alias="ODATA_INCOMING_EXTRA_FIELDS")
    odata_incoming_defaults_file: str = Field(
        default="data/odata_incoming_defaults.json",
        alias="ODATA_INCOMING_DEFAULTS_FILE",
    )
    odata_organization_keys: str = Field(default="", alias="ODATA_ORGANIZATION_KEYS")
    odata_department_keys: str = Field(default="", alias="ODATA_DEPARTMENT_KEYS")
    odata_organization_keys_file: str = Field(
        default="data/odata_organization_keys.json",
        alias="ODATA_ORGANIZATION_KEYS_FILE",
    )
    odata_department_keys_file: str = Field(
        default="data/odata_department_keys.json",
        alias="ODATA_DEPARTMENT_KEYS_FILE",
    )
    odata_routing_rules_path: str = Field(default="", alias="ODATA_ROUTING_RULES_PATH")
    odata_attached_file_field_map_file: str = Field(
        default="data/odata_attached_file_field_map.json",
        alias="ODATA_ATTACHED_FILE_FIELD_MAP_FILE",
    )
    odata_attach_files_enabled: bool = Field(default=True, alias="ODATA_ATTACH_FILES_ENABLED")
    # database — ВИнформационнойБазе + Base64 (шаблон АЛ00-000760); volume — ВТомахНаДиске + stream PUT
    odata_file_storage_mode: str = Field(default="database", alias="ODATA_FILE_STORAGE_MODE")
    odata_file_volume_key: str = Field(
        default="21886495-364e-11ea-82f2-ac1f6b05524c",
        alias="ODATA_FILE_VOLUME_KEY",
    )
    odata_file_author_key: str = Field(default="", alias="ODATA_FILE_AUTHOR_KEY")
    # UNC/локальный корень тома 1С (fallback: OData Catalog_ТомаХраненияФайлов → ПолныйПутьWindows)
    odata_file_volume_root: str = Field(default="", alias="ODATA_FILE_VOLUME_ROOT")
    # Записать байты на том ДО OData POST (как drag-drop Outlook); без этого thick client не открывает .msg
    odata_file_volume_preupload: bool = Field(
        default=False, alias="ODATA_FILE_VOLUME_PREUPLOAD"
    )
    # Локальный staging перед OData POST: аудит байт и round-trip проверка
    odata_attach_staging_enabled: bool = Field(
        default=True, alias="ODATA_ATTACH_STAGING_ENABLED"
    )
    odata_attach_staging_dir: str = Field(
        default="data/temp/erp_attach_staging",
        alias="ODATA_ATTACH_STAGING_DIR",
    )
    odata_attach_staging_delete_after_success: bool = Field(
        default=True, alias="ODATA_ATTACH_STAGING_DELETE_AFTER_SUCCESS"
    )
    odata_attach_staging_keep_on_failure: bool = Field(
        default=True, alias="ODATA_ATTACH_STAGING_KEEP_ON_FAILURE"
    )
    odata_timeout_sec: float = Field(default=60.0, alias="ODATA_TIMEOUT_SEC")
    celery_broker_url: str = Field(
        default="amqp://guest:guest@localhost:5672//", alias="CELERY_BROKER_URL"
    )

    # Внешние сервисы платформы (при use_stubs=false)
    # openai_compat | deepseek | auto
    llm_provider: str = Field(default="auto", alias="LLM_PROVIDER")
    llm_gateway_url: str = Field(default="", alias="LLM_GATEWAY_URL")
    llm_gateway_api_key: str = Field(default="", alias="LLM_GATEWAY_API_KEY")
    llm_default_model: str = Field(default="qwen/qwen3.5-9b", alias="LLM_DEFAULT_MODEL")
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        alias="DEEPSEEK_BASE_URL",
    )
    document_service_url: str = Field(default="", alias="DOCUMENT_SERVICE_URL")
    integration_service_url: str = Field(default="", alias="INTEGRATION_SERVICE_URL")
    vault_addr: str = Field(default="", alias="VAULT_ADDR")

    agent_version: str = Field(default="0.1.0", alias="AGENT_VERSION")

    # Статистика / журнал изменений (change_events + фоновый экспорт)
    stats_export_dir: str = Field(default="data/stats", alias="STATS_EXPORT_DIR")
    stats_repo_root: str = Field(default="", alias="STATS_REPO_ROOT")
    stats_start_time: str = Field(default="2026-07-08 08:35:00", alias="STATS_START_TIME")
    stats_timezone: str = Field(default="Europe/Moscow", alias="STATS_TIMEZONE")
    stats_export_interval_sec: int = Field(default=600, alias="STATS_EXPORT_INTERVAL_SEC")

    # Резервная синхронизация JSON / PG → Qdrant (celery-beat)
    rag_sync_interval_sec: int = Field(default=3600, alias="RAG_SYNC_INTERVAL_SEC")

    # Семантическая индексация писем (тело + вложения) → Qdrant через BGE
    email_rag_enabled: bool = Field(default=True, alias="EMAIL_RAG_ENABLED")
    embedding_base_url: str = Field(
        default="http://192.168.1.157:1234/v1",
        alias="EMBEDDING_BASE_URL",
    )
    embedding_model: str = Field(default="BAAI/bge-m3", alias="EMBEDDING_MODEL")
    embedding_vector_size: int = Field(default=1024, alias="EMBEDDING_VECTOR_SIZE")
    embedding_api_key: str = Field(default="", alias="EMBEDDING_API_KEY")
    embedding_timeout_sec: float = Field(default=60.0, alias="EMBEDDING_TIMEOUT_SEC")
    email_rag_max_source_chars: int = Field(default=50_000, alias="EMAIL_RAG_MAX_SOURCE_CHARS")
    email_rag_chunk_chars: int = Field(default=4000, alias="EMAIL_RAG_CHUNK_CHARS")
    email_rag_chunk_overlap: int = Field(default=200, alias="EMAIL_RAG_CHUNK_OVERLAP")
    email_rag_min_chars: int = Field(default=40, alias="EMAIL_RAG_MIN_CHARS")
    email_rag_sync_batch_size: int = Field(default=50, alias="EMAIL_RAG_SYNC_BATCH_SIZE")
    email_rag_sync_interval_sec: int = Field(default=900, alias="EMAIL_RAG_SYNC_INTERVAL_SEC")
    dept_corrections_sync_batch_size: int = Field(
        default=100, alias="DEPT_CORRECTIONS_SYNC_BATCH_SIZE"
    )
    dept_corrections_sync_interval_sec: int = Field(
        default=3600, alias="DEPT_CORRECTIONS_SYNC_INTERVAL_SEC"
    )
    bge_department_routing_enabled: bool = Field(
        default=False, alias="BGE_DEPARTMENT_ROUTING_ENABLED"
    )
    bge_dept_min_score: float = Field(default=0.80, alias="BGE_DEPT_MIN_SCORE")
    bge_dept_top_k: int = Field(default=3, alias="BGE_DEPT_TOP_K")
    bge_routing_enabled_since: str = Field(
        default="", alias="BGE_ROUTING_ENABLED_SINCE"
    )

    @property
    def mailbox_list(self) -> list[str]:
        return [m.strip() for m in self.mailboxes.split(",") if m.strip()]

    @property
    def trusted_domain_list(self) -> list[str]:
        """Доверенные домены отправителей: явный список + домены из MAILBOXES."""
        explicit = [
            d.strip().lower().lstrip("@")
            for d in self.trusted_sender_domains.split(",")
            if d.strip()
        ]
        from_mailboxes = [
            m.rsplit("@", 1)[-1].lower()
            for m in self.mailbox_list
            if "@" in m
        ]
        seen: set[str] = set()
        merged: list[str] = []
        for domain in explicit + from_mailboxes:
            if domain and domain not in seen:
                seen.add(domain)
                merged.append(domain)
        return merged

    @property
    def effective_llm_provider(self) -> str:
        """deepseek | openai_compat."""
        explicit = (self.llm_provider or "auto").strip().lower()
        if explicit in {"openai_compat", "deepseek"}:
            return explicit
        if self.deepseek_api_key:
            return "deepseek"
        return "openai_compat"

    @property
    def effective_llm_api_key(self) -> str:
        if self.effective_llm_provider == "deepseek":
            return (self.deepseek_api_key or self.llm_gateway_api_key).strip()
        return (self.llm_gateway_api_key or "").strip()

    @property
    def effective_llm_base_url(self) -> str:
        if self.effective_llm_provider == "deepseek":
            return (
                self.deepseek_base_url
                or self.llm_gateway_url
                or "https://api.deepseek.com/v1"
            ).rstrip("/")
        return self.llm_gateway_url

    @property
    def llm_configured(self) -> bool:
        """Есть реальный LLM (DeepSeek / OpenAI-compatible URL)."""
        if self.effective_llm_provider == "deepseek":
            return bool(self.effective_llm_api_key)
        return bool(self.llm_gateway_url)

    @property
    def erp_integration_mode(self) -> str:
        """Режим интеграции с 1С: odata | http | stub."""
        explicit = (self.erp_mode or "").strip().lower()
        if explicit in {"odata", "http", "stub"}:
            return explicit
        if self.use_odata_erp and self.odata_base_url:
            return "odata"
        if self.integration_service_url:
            return "http"
        return "stub"

    def service_modes(self) -> dict[str, str]:
        """Краткая сводка: stub | real | qdrant для диагностики."""
        if self.use_stubs:
            rag = "stub" if self.rag_backend == "stub" else f"qdrant({self.qdrant_url})"
            return {
                "mode": "stubs",
                "llm": "stub",
                "rag": rag,
                "documents": "local(pdf/docx/xlsx/ocr)",
                "integration": "stub",
            }
        erp = self.erp_integration_mode
        if erp == "odata":
            integration = f"odata({self.odata_base_url}/{self.odata_incoming_doc_entity})"
        elif erp == "http":
            integration = f"http({self.integration_service_url})"
        else:
            integration = "stub(no ERP URL)"
        llm_url = self.effective_llm_base_url
        if self.effective_llm_provider == "deepseek" and self.effective_llm_api_key:
            llm_label = f"deepseek({llm_url}, model={self.llm_default_model})"
        elif llm_url:
            llm_label = f"real({llm_url})"
        else:
            llm_label = "stub(no URL)"
        embedding = (
            f"bge({self.embedding_base_url}, model={self.embedding_model})"
            if self.email_rag_enabled and self.embedding_base_url
            else "disabled"
        )
        return {
            "mode": "production",
            "llm": llm_label,
            "rag": f"qdrant({self.qdrant_url})" if self.rag_backend == "qdrant" else "stub",
            "email_vectors": embedding,
            "documents": f"http({self.document_service_url})"
            if self.document_service_url
            else "local(pdf/docx/xlsx/ocr)",
            "integration": integration,
            "erp_mode": erp,
        }


_settings: Settings | None = None


def get_settings() -> Settings:
    """Singleton-доступ к настройкам."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Сброс singleton (тесты)."""
    global _settings
    _settings = None


def print_startup_config() -> None:
    """Краткая диагностика конфигурации при старте worker/beat."""
    settings = get_settings()
    env_status = "found" if ENV_FILE.is_file() else "MISSING — copy .env.example .env"
    pwd_status = "ok" if settings.imap_password else "MISSING"
    modes = settings.service_modes()
    print(
        f"[agent-pochta] env={ENV_FILE} ({env_status}); "
        f"mailboxes={settings.mailbox_list}; "
        f"imap={settings.imap_host}:{settings.imap_port}; "
        f"IMAP_PASSWORD={pwd_status}; "
        f"services={modes}"
    )
