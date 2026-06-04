from __future__ import annotations
from typing import Any
import httpx
from app.core.config import settings
class LLMGateway:
    def __init__(self) -> None:
        self.base_url = settings.LLM_GATEWAY_BASE_URL.rstrip("/")
        self.api_key = settings.LLM_GATEWAY_API_KEY or settings.OPENAI_API_KEY
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
    async def chat(self, messages: list[dict[str, str]], model: str | None = None, **kwargs: Any) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", json={"model": model or settings.LLM_DEFAULT_MODEL, "messages": messages, **kwargs}, headers=self._headers())
            resp.raise_for_status()
            return resp.json()
    async def embeddings(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{self.base_url}/embeddings", json={"model": model or settings.LLM_EMBEDDING_MODEL, "input": texts}, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"] for item in data["data"]]
llm_gateway = LLMGateway()
