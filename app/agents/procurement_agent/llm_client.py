from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.config import settings

STYLE_ANTHROPIC = "anthropic"

PROCUREMENT_LLM_PROVIDER = {
    "style": STYLE_ANTHROPIC,
    "base_url": settings.PROCUREMENT_LLM_BASE_URL,
    "model": settings.PROCUREMENT_LLM_MODEL,
    "models": settings.PROCUREMENT_LLM_MODELS,
    "api_key_env": "CLAUDE_API_KEY,OPENAI_API_KEY_CLAUDE",
    "display_name": "Claude",
    "supports_reasoning": settings.PROCUREMENT_LLM_SUPPORTS_REASONING,
    "discover_models": settings.PROCUREMENT_LLM_DISCOVER_MODELS,
}


class ProcurementLLMConfigurationError(RuntimeError):
    pass


class ProcurementClaudeClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 180,
    ) -> None:
        self.base_url = (base_url or settings.PROCUREMENT_LLM_BASE_URL).rstrip("/")
        self.model = model or settings.PROCUREMENT_LLM_MODEL
        self.api_key = (
            api_key
            or settings.CLAUDE_API_KEY
            or settings.OPENAI_API_KEY_CLAUDE
        )
        self.timeout_seconds = timeout_seconds

    async def chat(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        if not self.api_key:
            raise ProcurementLLMConfigurationError(
                "CLAUDE_API_KEY or OPENAI_API_KEY_CLAUDE is not configured"
            )
        system_parts = [
            message["content"]
            for message in messages
            if message.get("role") == "system"
        ]
        anthropic_messages = [
            {
                "role": message["role"],
                "content": message["content"],
            }
            for message in messages
            if message.get("role") in {"user", "assistant"}
        ]
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": 0.1,
            "messages": anthropic_messages,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        response = await self._request("POST", "messages", json=payload)
        body = response.json()
        text = "".join(
            block.get("text", "")
            for block in body.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        return {"choices": [{"message": {"role": "assistant", "content": text}}]}

    async def discover_models(self) -> list[str]:
        if not self.api_key:
            raise ProcurementLLMConfigurationError(
                "CLAUDE_API_KEY or OPENAI_API_KEY_CLAUDE is not configured"
            )
        response = await self._request("GET", "models")
        body = response.json()
        return [
            str(item["id"])
            for item in body.get("data", [])
            if isinstance(item, dict) and item.get("id")
        ]

    def _endpoint(self, resource: str) -> str:
        prefix = self.base_url if self.base_url.endswith("/v1") else f"{self.base_url}/v1"
        return f"{prefix}/{resource}"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def _request(
        self,
        method: str,
        resource: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.request(
                        method,
                        self._endpoint(resource),
                        headers=self._headers(),
                        json=json,
                    )
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5 * (2**attempt))
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                await asyncio.sleep(0.5 * (2**attempt))
                continue
            response.raise_for_status()
            return response
        raise RuntimeError("unreachable")


procurement_llm_client = ProcurementClaudeClient()

__all__ = [
    "PROCUREMENT_LLM_PROVIDER",
    "STYLE_ANTHROPIC",
    "ProcurementClaudeClient",
    "ProcurementLLMConfigurationError",
    "procurement_llm_client",
]
