"""Vault — хранение секретов (пароли IMAP, токены 1С, ключи LLM). Разделы 5.1–5.2."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from functools import lru_cache

from agent_pochta.config import ENV_FILE


@lru_cache(maxsize=1)
def _dotenv_file_values() -> dict[str, str]:
    """Ключи из PROJECT_ROOT/.env, не объявленные в Settings (напр. IMAP_USER_*)."""
    if not ENV_FILE.is_file():
        return {}
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


class VaultClient(ABC):
    @abstractmethod
    def get_secret(self, key: str) -> str | None:
        ...


class StubVaultClient(VaultClient):
    """Заглушка: os.environ, затем PROJECT_ROOT/.env (per-mailbox IMAP_USER_*)."""

    def get_secret(self, key: str) -> str | None:
        env_value = os.environ.get(key)
        if env_value:
            return env_value
        file_value = _dotenv_file_values().get(key)
        if file_value:
            return file_value
        return None
