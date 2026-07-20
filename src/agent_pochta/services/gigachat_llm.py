"""HTTP-адаптер GigaChat API — OAuth + OpenAI-совместимый /chat/completions."""

from __future__ import annotations

import time
import uuid

import httpx

from agent_pochta.services.http_llm import ChatCompletionsLLMGateway

_DEFAULT_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
_DEFAULT_BASE_URL = "https://gigachat.devices.sberbank.ru/api/v1"
_TOKEN_REFRESH_MARGIN_SEC = 60.0


class GigaChatLLMGateway(ChatCompletionsLLMGateway):
    """GigaChat: Basic OAuth → Bearer, затем POST /chat/completions."""

    def __init__(
        self,
        credentials: str,
        *,
        scope: str = "GIGACHAT_API_PERS",
        auth_url: str = _DEFAULT_AUTH_URL,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = "GigaChat",
        timeout_sec: float = 120.0,
        verify_ssl: bool = False,
    ) -> None:
        self._credentials = credentials.strip()
        self._scope = scope
        self._auth_url = auth_url.rstrip("/")
        self._access_token = ""
        self._token_expires_at = 0.0
        super().__init__(base_url, api_key="", model=model, timeout_sec=timeout_sec)
        self._http = httpx.Client(timeout=self._timeout, verify=verify_ssl)

    def _ensure_access_token(self) -> None:
        now = time.time()
        if self._access_token and now < self._token_expires_at - _TOKEN_REFRESH_MARGIN_SEC:
            return
        response = self._http.post(
            self._auth_url,
            data={"scope": self._scope},
            headers={
                "Authorization": f"Basic {self._credentials}",
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("access_token") or payload.get("tok") or "")
        if not token:
            raise RuntimeError("GigaChat OAuth: пустой access_token в ответе")
        expires_at = payload.get("expires_at") or payload.get("exp")
        if expires_at is not None:
            self._token_expires_at = float(expires_at) / 1000.0 if float(expires_at) > 1e12 else float(expires_at)
        else:
            self._token_expires_at = now + 1800.0
        self._access_token = token

    def _headers(self) -> dict[str, str]:
        self._ensure_access_token()
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._access_token}",
        }

    def _use_json_response_format(self) -> bool:
        return False
