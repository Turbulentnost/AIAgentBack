from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.core.config import settings


class TurboProjectError(RuntimeError):
    """Ошибка вызова TurboProject API."""


@dataclass(frozen=True)
class TurboProjectConfig:
    base_url: str
    email: str
    password: str
    timeout: int = 60


def build_turbo_project_config() -> TurboProjectConfig:
    base_url = (settings.TURBO_PROJECT_API_BASE_URL or "").strip().rstrip("/")
    email = (settings.TURBO_PROJECT_EMAIL or "").strip()
    password = settings.TURBO_PROJECT_PASSWORD or ""
    if not base_url:
        raise TurboProjectError("Не задан TURBO_PROJECT_API_BASE_URL в .env")
    if not email or not password:
        raise TurboProjectError("Не заданы TURBO_PROJECT_EMAIL / TURBO_PROJECT_PASSWORD в .env")
    return TurboProjectConfig(
        base_url=base_url,
        email=email,
        password=password,
        timeout=settings.TURBO_PROJECT_TIMEOUT,
    )


class TurboProjectClient:
    def __init__(self, config: TurboProjectConfig | None = None) -> None:
        self.config = config or build_turbo_project_config()
        self._token: str | None = None

    def login(self, *, force_refresh: bool = False) -> str:
        if self._token and not force_refresh:
            return self._token
        try:
            response = requests.post(
                f"{self.config.base_url}/api/auth/login",
                json={"email": self.config.email, "password": self.config.password},
                timeout=self.config.timeout,
            )
        except requests.RequestException as error:
            raise TurboProjectError(f"Не удалось подключиться к TurboProject: {error}") from error
        if not response.ok:
            raise TurboProjectError(
                f"TurboProject login HTTP {response.status_code}: {response.text[:500]}"
            )
        payload = response.json()
        token = payload.get("token")
        if not isinstance(token, str) or not token.strip():
            raise TurboProjectError("TurboProject login не вернул token")
        self._token = token.strip()
        return self._token

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        token = self.login()
        url = f"{self.config.base_url}{path}"
        try:
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=self.config.timeout,
            )
        except requests.RequestException as error:
            raise TurboProjectError(f"TurboProject GET {path} failed: {error}") from error
        if response.status_code == 401:
            token = self.login(force_refresh=True)
            try:
                response = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                    timeout=self.config.timeout,
                )
            except requests.RequestException as error:
                raise TurboProjectError(f"TurboProject GET {path} failed: {error}") from error
        if not response.ok:
            raise TurboProjectError(
                f"TurboProject GET {path} HTTP {response.status_code}: {response.text[:500]}"
            )
        return response.json()
