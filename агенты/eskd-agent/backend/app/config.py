"""Конфигурация API-шлюза ESKD Agent."""

import json

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model_service_url: str = "http://host.docker.internal:8765"
    cors_origins: str = "*"
    max_upload_mb: int = 200
    request_timeout_sec: float = 600.0
    database_url: str = "postgresql+asyncpg://eskd:eskd@postgres:5432/eskd_agent"
    storage_path: str = "/data/previews"
    uploads_path: str = "/data/uploads"

    # Integration layer (п. 4.8)
    integration_root: str = "/data/integration"
    integration_incoming_dir: str = "incoming"
    integration_processing_dir: str = "processing"
    integration_completed_dir: str = "completed"
    integration_error_dir: str = "error"
    integration_archive_dir: str = "archive"
    sed_archive_dir: str = "sed"
    integration_worker_enabled: bool = True
    integration_poll_sec: float = 15.0
    exchange_log_retention_days: int = 365
    webhook_max_retries: int = 5
    webhook_timeout_sec: float = 30.0
    closed_contour: bool = True

    # Auth / RBAC
    auth_mode: str = "dev"  # dev | api_key | ldap
    integration_api_key_required: bool = False
    ldap_server: str = ""
    ldap_base_dn: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_user_filter: str = "(sAMAccountName={username})"
    ldap_group_role_map: str = (
        '{"ESKD_Admins":"ESKD_Administrators","ESKD_Norm":"ESKD_NormControl"}'
    )
    dev_default_roles: str = "ESKD_Designers,ESKD_NormControl"

    # Two-stage ESKD pipeline (extract → rules → openrouter)
    eskd_pipeline_mode: str = "legacy"
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-4"
    openrouter_eval_model: str = "anthropic/claude-sonnet-4"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def ldap_group_role_mapping(self) -> dict[str, str]:
        try:
            return json.loads(self.ldap_group_role_map)
        except json.JSONDecodeError:
            return {}

    @property
    def dev_roles_list(self) -> list[str]:
        return [r.strip() for r in self.dev_default_roles.split(",") if r.strip()]


settings = Settings()
