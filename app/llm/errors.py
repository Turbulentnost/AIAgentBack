from __future__ import annotations

from app.core.config import settings


def format_llm_call_error(exc: Exception) -> str:
    detail = str(exc).strip() or repr(exc)
    lower = detail.lower()
    base_url = (settings.LLM_GATEWAY_BASE_URL or "").strip() or "(LLM_GATEWAY_BASE_URL не задан)"

    if any(
        marker in lower
        for marker in (
            "connecterror",
            "connection refused",
            "all connection attempts failed",
            "failed to connect",
            "name or service not known",
            "nodename nor servname",
        )
    ):
        return (
            f"Не удалось подключиться к LLM-серверу ({base_url}). "
            "Запустите LM Studio или другой OpenAI-compatible сервер, "
            "убедитесь что модель загружена, и проверьте LLM_GATEWAY_BASE_URL в .env."
        )

    return f"Ошибка вызова LLM ({type(exc).__name__}): {detail}"
