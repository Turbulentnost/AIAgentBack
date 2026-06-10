from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class OneCTokenPayload:
    token: str | None
    expires_at: datetime | None
    resolved_user: str | None
    resolved_user_source: str | None


class OneCAuthApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


async def request_onec_token(*, fio: str, password: str) -> OneCTokenPayload:
    base_url = settings.ONEC_AUTH_API_BASE_URL.rstrip("/")
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{base_url}/tasks",
            json={"fio": fio, "password": password},
        )
    if response.status_code >= 400:
        detail = response.text.strip() or response.reason_phrase
        raise OneCAuthApiError(
            f"Ошибка авторизации в 1С: {detail}",
            status_code=response.status_code,
        )
    data = response.json()
    expires_at = _parse_datetime(data.get("expires_at"))
    return OneCTokenPayload(
        token=data.get("token"),
        expires_at=expires_at,
        resolved_user=data.get("resolved_user"),
        resolved_user_source=data.get("resolved_user_source") or data.get("query"),
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)
