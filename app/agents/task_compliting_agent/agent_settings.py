from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

_AGENT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _AGENT_DIR.parent.parent.parent


class TaskCompletingAgentSettings(BaseSettings):
    """Настройки LLM только для этого агента."""

    model_config = SettingsConfigDict(
        # Сначала корень (ключ API), затем .env агента — переопределяет LLM_* .
        env_file=(_PROJECT_ROOT / ".env", _AGENT_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    LLM_PROVIDER: Literal["anthropic", "openai_compatible"] = "openai_compatible"
    OPENAI_API_KEY: str | None = None
    OPENAI_API_KEY_CLAUDE: str | None = None
    LLM_DEFAULT_MODEL: str = "claude-sonnet-4-20250514"
    LLM_BASE_URL: str = "https://api.anthropic.com/v1"
    LLM_MAX_TOKENS: int = 4096
    ANTHROPIC_VERSION: str = "2023-06-01"


@lru_cache
def get_agent_settings() -> TaskCompletingAgentSettings:
    return TaskCompletingAgentSettings()


agent_settings = get_agent_settings()
