"""Vault — хранение секретов (пароли IMAP, токены 1С, ключи LLM). Разделы 5.1–5.2."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod


class VaultClient(ABC):
    @abstractmethod
    def get_secret(self, key: str) -> str | None:
        ...


class StubVaultClient(VaultClient):
    """Заглушка: читает секреты из переменных окружения (для локальной разработки)."""

    def get_secret(self, key: str) -> str | None:
        return os.environ.get(key)
