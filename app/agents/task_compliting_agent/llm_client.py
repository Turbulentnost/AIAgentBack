from __future__ import annotations

import json
from typing import Any

import httpx

from app.agents.task_compliting_agent.agent_settings import agent_settings
from app.agents.task_compliting_agent.json_parse import (
    LM_STUDIO_RESPONSE_FORMAT,
    extract_json_payload,
)

_ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-4-20250514"
_OPENAI_DEFAULT_MODEL = "gpt-4o"


def _api_key() -> str | None:
    if agent_settings.LLM_PROVIDER == "anthropic":
        return agent_settings.OPENAI_API_KEY_CLAUDE
    return agent_settings.OPENAI_API_KEY or agent_settings.OPENAI_API_KEY_CLAUDE


def _resolve_model(requested: str | None) -> str:
    model = (requested or agent_settings.LLM_DEFAULT_MODEL).strip()
    if agent_settings.LLM_PROVIDER == "anthropic" and not model.lower().startswith("claude"):
        return _ANTHROPIC_DEFAULT_MODEL
    if agent_settings.LLM_PROVIDER == "openai_compatible":
        base = agent_settings.LLM_BASE_URL.lower()
        if "api.openai.com" in base and not model.lower().startswith("gpt"):
            return _OPENAI_DEFAULT_MODEL
    return model


def _merge_assistant_message(message: dict[str, Any]) -> str:
    """LM Studio / Qwen3.5 может писать JSON в reasoning_content, а в content — рассуждения."""
    content = str(message.get("content") or "").strip()
    reasoning = str(message.get("reasoning_content") or "").strip()
    for text in (reasoning, content, f"{reasoning}\n{content}".strip()):
        if not text:
            continue
        try:
            extract_json_payload(text)
            return text
        except json.JSONDecodeError:
            continue
    return content or reasoning


def _normalize_chat_response(data: dict[str, Any]) -> dict[str, Any]:
    choices = data.get("choices") or []
    if not choices:
        return data
    message = choices[0].get("message") or {}
    merged = _merge_assistant_message(message)
    if merged:
        message["content"] = merged
        choices[0]["message"] = message
    return data


def _split_messages(messages: list[dict[str, str]]) -> tuple[str | None, list[dict[str, str]]]:
    system_parts: list[str] = []
    conversation: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role", "user")
        content = str(message.get("content", ""))
        if role == "system":
            system_parts.append(content)
        elif role in {"user", "assistant"}:
            conversation.append({"role": role, "content": content})
        else:
            conversation.append({"role": "user", "content": content})
    system = "\n\n".join(system_parts).strip() or None
    return system, conversation


async def _chat_anthropic(
    messages: list[dict[str, str]],
    model: str,
    max_tokens: int,
) -> dict[str, Any]:
    api_key = agent_settings.OPENAI_API_KEY_CLAUDE
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY_CLAUDE не задан (корневой .env или app/agents/task_compliting_agent/.env)"
        )

    system, conversation = _split_messages(messages)
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": conversation,
    }
    if system:
        payload["system"] = system

    base_url = agent_settings.LLM_BASE_URL.rstrip("/")
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": agent_settings.ANTHROPIC_VERSION,
        "x-api-key": api_key,
    }

    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        resp = await client.post(f"{base_url}/messages", json=payload, headers=headers)
        if resp.is_error:
            raise httpx.HTTPStatusError(
                f"{resp.status_code} {resp.reason_phrase}; model={model!r}; body={resp.text[:500]}",
                request=resp.request,
                response=resp,
            )
        data = resp.json()

    text_blocks = [
        block.get("text", "")
        for block in data.get("content", [])
        if block.get("type") == "text"
    ]
    content = "\n".join(part for part in text_blocks if part).strip()
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _is_local_gateway(base_url: str) -> bool:
    host = base_url.lower()
    return any(
        marker in host
        for marker in ("localhost", "127.0.0.1", ":1234", "192.168.")
    )


async def _chat_openai_compatible(
    messages: list[dict[str, str]],
    model: str,
) -> dict[str, Any]:
    base_url = agent_settings.LLM_BASE_URL.rstrip("/")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = _api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif not _is_local_gateway(base_url):
        raise ValueError(
            "OPENAI_API_KEY не задан. Для LM Studio укажите LLM_BASE_URL на локальный сервер."
        )

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": agent_settings.LLM_MAX_TOKENS,
    }
    if _is_local_gateway(base_url):
        payload["response_format"] = LM_STUDIO_RESPONSE_FORMAT
    else:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers=headers,
        )
        if resp.is_error:
            raise httpx.HTTPStatusError(
                f"{resp.status_code} {resp.reason_phrase}; model={model!r}; body={resp.text[:500]}",
                request=resp.request,
                response=resp,
            )
        return _normalize_chat_response(resp.json())


async def chat_completion(
    messages: list[dict[str, str]],
    model: str | None = None,
) -> dict[str, Any]:
    payload_model = _resolve_model(model)
    if agent_settings.LLM_PROVIDER == "anthropic":
        return await _chat_anthropic(
            messages,
            payload_model,
            agent_settings.LLM_MAX_TOKENS,
        )
    return await _chat_openai_compatible(messages, payload_model)
