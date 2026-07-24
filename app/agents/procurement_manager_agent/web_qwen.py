"""Qwen (LM Studio) helpers for procurement web search enrichment.

Parses real fetched page / SERP text into structured supplier fields.
Never invents suppliers — only extracts from provided text.
"""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Protocol

import httpx

ChatFn = Callable[..., Awaitable[dict[str, Any]]]

DEFAULT_QWEN_MODEL = "qwen/qwen3.5-9b"
DEFAULT_QWEN_TIMEOUT_SECONDS = 25.0
DEFAULT_PAGE_CHARS = 10_000

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

_PAGE_SYSTEM = (
    "Ты помощник закупщика. Из страницы товара/поставщика извлеки факты. "
    "Ответь ТОЛЬКО одним JSON-объектом без markdown и без пояснений. "
    "Схема: {"
    '"title": string|null, '
    '"unit_price": number|null, '
    '"approx_cost": number|null, '
    '"city": string|null, '
    '"lead_time_days": number|null, '
    '"delivery_hint": string|null, '
    '"product_match_confidence": number'
    "}. "
    "Цены только в рублях (число без валюты). "
    "Не выдумывай цену, город и срок — если нет в тексте, ставь null. "
    "product_match_confidence от 0 до 1: насколько страница про запрошенный товар."
)

_QUERY_SYSTEM = (
    "Сожми запрос для поиска поставщика в Bing. "
    "Ответ: только короткая русская фраза до 12 слов, без кавычек и пояснений. "
    "Добавь слово «купить» или «поставщик» если уместно."
)


class SupportsChat(Protocol):
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        *,
        timeout: float | httpx.Timeout | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]: ...


def _env_or_setting(env_key: str, setting_attr: str | None = None) -> str:
    value = (os.environ.get(env_key) or "").strip()
    if value:
        return value
    if not setting_attr:
        return ""
    try:
        from app.core.config import settings

        return str(getattr(settings, setting_attr, None) or "").strip()
    except Exception:
        return ""


def _truthy(raw: str | None, *, default: bool) -> bool:
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().casefold() in {"1", "true", "yes", "on"}


def qwen_web_enabled() -> bool:
    """PROCUREMENT_WEB_USE_QWEN — settings default true; env overrides."""
    raw = os.environ.get("PROCUREMENT_WEB_USE_QWEN")
    if raw is not None and str(raw).strip():
        return _truthy(raw, default=True)
    try:
        from app.core.config import settings

        return bool(getattr(settings, "PROCUREMENT_WEB_USE_QWEN", True))
    except Exception:
        environment = (
            os.environ.get("ENVIRONMENT") or "dev"
        ).strip().casefold()
        return environment in {"dev", "test"}


def qwen_refine_query_enabled() -> bool:
    raw = os.environ.get("PROCUREMENT_WEB_QWEN_REFINE_QUERY")
    if raw is not None and str(raw).strip():
        return _truthy(raw, default=False)
    try:
        from app.core.config import settings

        return bool(getattr(settings, "PROCUREMENT_WEB_QWEN_REFINE_QUERY", False))
    except Exception:
        return False


def resolve_qwen_gateway_url() -> str:
    """Prefer LLM_GATEWAY_URL / LLM_GATEWAY_BASE_URL, then vision LM Studio URL."""
    for key, attr in (
        ("LLM_GATEWAY_URL", "LLM_GATEWAY_BASE_URL"),
        ("LLM_GATEWAY_BASE_URL", "LLM_GATEWAY_BASE_URL"),
        ("VISION_LM_STUDIO_BASE_URL", "VISION_LM_STUDIO_BASE_URL"),
    ):
        value = _env_or_setting(key, attr)
        if value:
            return value.rstrip("/")
    return ""


def resolve_qwen_model() -> str:
    for key, attr in (
        ("PROCUREMENT_WEB_QWEN_MODEL", "PROCUREMENT_WEB_QWEN_MODEL"),
        ("LLM_DEFAULT_MODEL", "LLM_DEFAULT_MODEL"),
        ("VISION_LM_STUDIO_MODEL", "VISION_LM_STUDIO_MODEL"),
    ):
        value = _env_or_setting(key, attr)
        if value:
            return value
    return DEFAULT_QWEN_MODEL


def resolve_qwen_api_key() -> str | None:
    for key, attr in (
        ("LLM_GATEWAY_API_KEY", "LLM_GATEWAY_API_KEY"),
        ("OPENAI_API_KEY_CLAUDE", "OPENAI_API_KEY_CLAUDE"),
        ("OPENAI_API_KEY", "OPENAI_API_KEY"),
    ):
        value = _env_or_setting(key, attr)
        if value:
            return value
    return None


def qwen_timeout_seconds() -> float:
    raw = _env_or_setting(
        "PROCUREMENT_WEB_QWEN_TIMEOUT_SECONDS",
        "PROCUREMENT_WEB_QWEN_TIMEOUT_SECONDS",
    )
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return DEFAULT_QWEN_TIMEOUT_SECONDS


def strip_think_blocks(text: str) -> str:
    cleaned = _THINK_RE.sub("", text or "")
    return cleaned.strip()


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract a JSON object from model output (handles fences / trailing text)."""
    raw = strip_think_blocks(text)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
    return {}


def _use_json_response_format(base_url: str) -> bool:
    """LM Studio / local vLLM often reject response_format with HTTP 400."""
    host = (base_url or "").lower()
    if "openrouter.ai" in host or "api.openai.com" in host or "groq.com" in host:
        return True
    return False


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _page_text_for_prompt(html_or_text: str, *, max_chars: int = DEFAULT_PAGE_CHARS) -> str:
    text = html_or_text or ""
    if "<" in text and ">" in text:
        # Light cleanup so the model sees readable content, not a full DOM dump.
        text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
        text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def normalize_qwen_page_fields(
    data: dict[str, Any],
    *,
    product_query: str | None = None,
) -> dict[str, Any] | None:
    """Map raw LLM JSON to enrichment fields; return None if unusable."""
    if not isinstance(data, dict) or not data:
        return None
    price = _to_decimal(data.get("unit_price"))
    if price is None:
        price = _to_decimal(data.get("approx_cost"))
    if price is not None and not (Decimal("1") <= price <= Decimal("50000000")):
        price = None

    city = data.get("city")
    city_out = str(city).strip()[:80] if isinstance(city, str) and city.strip() else None

    title = data.get("title")
    title_out = str(title).strip()[:240] if isinstance(title, str) and title.strip() else None

    delivery = data.get("delivery_hint")
    delivery_out = (
        str(delivery).strip()[:160] if isinstance(delivery, str) and delivery.strip() else None
    )

    lead_raw = data.get("lead_time_days")
    lead_days: int | None = None
    if lead_raw is not None and str(lead_raw).strip() != "":
        try:
            lead_days = int(float(str(lead_raw).replace(",", ".")))
            if lead_days < 0 or lead_days > 3650:
                lead_days = None
        except (TypeError, ValueError):
            lead_days = None
    if lead_days is not None and not delivery_out:
        delivery_out = f"срок поставки ~{lead_days} дн."

    confidence = _to_decimal(data.get("product_match_confidence"))
    if confidence is not None:
        if confidence > 1:
            confidence = confidence / Decimal("100")
        confidence = max(Decimal("0"), min(Decimal("1"), confidence))

    # Require at least one useful signal; empty JSON is treated as failure → regex fallback.
    if price is None and not city_out and not title_out and not delivery_out:
        return None

    return {
        "unit_price": price,
        "city": city_out,
        "title": title_out,
        "delivery_hint": delivery_out,
        "lead_time_days": lead_days,
        "product_match_confidence": confidence,
        "product_query": (product_query or "").strip() or None,
        "enrichment_source": "qwen",
        # Rating is left to regex / page meta — do not invent from LLM.
        "rating": None,
    }


def merge_parsed_fields(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    """Prefer primary (Qwen) values; fill gaps from regex fallback."""
    merged = dict(fallback)
    for key in ("unit_price", "city", "title", "delivery_hint", "rating"):
        value = primary.get(key)
        if value is None or value == "":
            continue
        merged[key] = value
    for key in ("lead_time_days", "product_match_confidence", "enrichment_source", "product_query"):
        if primary.get(key) is not None:
            merged[key] = primary[key]
    return merged


async def _chat_via_http(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    timeout: float,
    api_key: str | None,
) -> str:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
    }
    use_format = _use_json_response_format(base_url)
    if use_format:
        payload["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 400 or "response_format" not in payload:
                raise
            payload.pop("response_format", None)
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
        data = resp.json()
    return str(data["choices"][0]["message"]["content"] or "")


async def _chat_content(
    messages: list[dict[str, str]],
    *,
    chat_fn: ChatFn | SupportsChat | None = None,
    timeout: float | None = None,
) -> str:
    timeout_sec = float(timeout if timeout is not None else qwen_timeout_seconds())
    model = resolve_qwen_model()
    if chat_fn is not None:
        if hasattr(chat_fn, "chat"):
            response = await chat_fn.chat(messages, model=model, timeout=timeout_sec)  # type: ignore[union-attr]
        else:
            response = await chat_fn(messages, model=model, timeout=timeout_sec)  # type: ignore[operator]
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                return str(message.get("content") or "")
            # Allow tests to return {content: "..."} or the parsed object directly.
            if "content" in response:
                return str(response.get("content") or "")
            return json.dumps(response, ensure_ascii=False)
        return str(response)

    base_url = resolve_qwen_gateway_url()
    if not base_url:
        raise RuntimeError("LLM gateway URL is not configured")

    # Prefer platform gateway when its base_url matches (same LM Studio).
    try:
        from app.llm.gateway import llm_gateway

        if (llm_gateway.base_url or "").rstrip("/") == base_url.rstrip("/"):
            response = await llm_gateway.chat(messages, model=model, timeout=timeout_sec)
            choices = response.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                return str(message.get("content") or "")
    except Exception:
        pass

    return await _chat_via_http(
        base_url=base_url,
        model=model,
        messages=messages,
        timeout=timeout_sec,
        api_key=resolve_qwen_api_key(),
    )


async def extract_product_fields_with_qwen(
    html_or_text: str,
    *,
    product_query: str | None = None,
    page_url: str | None = None,
    chat_fn: ChatFn | SupportsChat | None = None,
    timeout: float | None = None,
) -> dict[str, Any] | None:
    """Ask Qwen to extract structured product fields from fetched page text."""
    if not qwen_web_enabled():
        return None
    page = _page_text_for_prompt(html_or_text)
    if len(page) < 40:
        return None
    if chat_fn is None and not resolve_qwen_gateway_url():
        return None

    user_payload = {
        "product_query": (product_query or "").strip() or None,
        "page_url": page_url,
        "page_text": page,
    }
    messages = [
        {"role": "system", "content": _PAGE_SYSTEM},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    try:
        content = await _chat_content(messages, chat_fn=chat_fn, timeout=timeout)
    except Exception:
        return None
    return normalize_qwen_page_fields(
        parse_json_object(content),
        product_query=product_query,
    )


async def refine_search_query_with_qwen(
    query: str,
    *,
    chat_fn: ChatFn | SupportsChat | None = None,
    timeout: float | None = None,
) -> str:
    """Optionally shorten/clarify a Russian Bing query. Returns original on failure."""
    original = (query or "").strip()
    if not original:
        return original
    if not qwen_web_enabled() or not qwen_refine_query_enabled():
        return original
    if chat_fn is None and not resolve_qwen_gateway_url():
        return original

    messages = [
        {"role": "system", "content": _QUERY_SYSTEM},
        {"role": "user", "content": original[:500]},
    ]
    try:
        content = await _chat_content(messages, chat_fn=chat_fn, timeout=timeout)
    except Exception:
        return original
    refined = strip_think_blocks(content)
    refined = refined.strip().strip("\"'«»").strip()
    # Reject empty / too long / JSON-looking answers.
    if not refined or len(refined) > 160 or refined.startswith("{"):
        return original
    refined = _WS_RE.sub(" ", refined)
    return refined[:160]


__all__ = [
    "DEFAULT_QWEN_MODEL",
    "extract_product_fields_with_qwen",
    "merge_parsed_fields",
    "normalize_qwen_page_fields",
    "parse_json_object",
    "qwen_refine_query_enabled",
    "qwen_timeout_seconds",
    "qwen_web_enabled",
    "refine_search_query_with_qwen",
    "resolve_qwen_gateway_url",
    "resolve_qwen_model",
    "strip_think_blocks",
]
