"""Health checks for VLM (model service) and LLM (OpenRouter / LM Studio)."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings


def _is_private_host(host: str) -> bool:
    if host in {"127.0.0.1", "localhost"}:
        return True
    if host.startswith("192.168.") or host.startswith("10."):
        return True
    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
            return 16 <= second <= 31
        except (IndexError, ValueError):
            pass
    return False


def _llm_backend_label(base_url: str) -> str:
    host = urlparse(base_url).hostname or ""
    if _is_private_host(host):
        return "lmstudio"
    return "openrouter"


def _llm_location(backend: str, base_url: str) -> str:
    if backend != "lmstudio":
        return "remote"
    host = urlparse(base_url).hostname or ""
    if host in {"127.0.0.1", "localhost"}:
        return "local"
    if _is_private_host(host):
        return "lan"
    return "remote"


def _is_local_vlm_backend(backend: str) -> bool:
    normalized = backend.strip().lower()
    return normalized in {"", "local", "gemma"}


def _vlm_inference_target(backend: str, base_url: str | None = None) -> str | None:
    normalized = backend.strip().lower()
    if normalized == "openrouter":
        return "openrouter.ai"
    if normalized == "lmstudio" and base_url:
        return _format_target(base_url)
    if _is_local_vlm_backend(normalized):
        return None
    return normalized or None


async def check_llm_health(*, timeout: float = 10.0) -> dict[str, Any]:
    """Ping LLM gateway configured in backend settings."""
    base_url = settings.openrouter_base_url.rstrip("/")
    model = settings.openrouter_eval_model or settings.openrouter_model
    api_key = settings.openrouter_api_key.strip()
    backend = _llm_backend_label(base_url)
    is_local = backend == "lmstudio"

    result: dict[str, Any] = {
        "backend": backend,
        "model": model,
        "base_url": base_url,
        "configured": bool(api_key or is_local),
        "required": settings.eskd_pipeline_mode.strip().lower() == "two_stage",
    }

    if not api_key and not is_local:
        result.update({"reachable": False, "error": "API key missing (OPENROUTER_API_KEY)"})
        return result

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url}/models", headers=headers)
            resp.raise_for_status()
        result.update({"reachable": True, "ping_ms": round((time.perf_counter() - t0) * 1000, 1)})
    except Exception as exc:
        result.update(
            {
                "reachable": False,
                "ping_ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": str(exc),
            }
        )

    result["target"] = _format_target(base_url)
    result["location"] = _llm_location(backend, base_url)
    return result


def _format_target(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = parsed.hostname or base_url
    if parsed.port:
        return f"{host}:{parsed.port}"
    return host


def build_vlm_status(
    model_payload: dict[str, Any],
    *,
    ping_ms: float,
    reachable: bool,
    service_url: str,
) -> dict[str, Any]:
    vlm = model_payload.get("vlm") if isinstance(model_payload.get("vlm"), dict) else {}
    base_url = service_url.rstrip("/")
    backend = str(vlm.get("backend") or model_payload.get("vlm_backend") or "local")
    gateway_target = _format_target(base_url)
    is_local_inference = _is_local_vlm_backend(backend)

    status: dict[str, Any] = {
        "reachable": reachable,
        "ping_ms": ping_ms,
        "location": "lan" if backend.strip().lower() == "lmstudio" else ("local" if is_local_inference else "remote"),
        "base_url": base_url,
        "gateway_target": gateway_target,
        "target": gateway_target,
        "backend": backend,
        "model": vlm.get("model") or model_payload.get("vlm_model") or model_payload.get("model_path"),
        "model_loaded": model_payload.get("model_loaded"),
        "model_path": model_payload.get("model_path"),
        "adapter_path": model_payload.get("adapter_path"),
        "load_seconds": model_payload.get("load_seconds"),
        "error": model_payload.get("error"),
    }

    inference_target = _vlm_inference_target(backend, str(vlm.get("base_url") or model_payload.get("base_url") or ""))
    if inference_target:
        status["inference_target"] = inference_target
    vlm_base = vlm.get("base_url")
    if vlm_base:
        status["base_url"] = vlm_base
    return status


def build_llm_status(model_payload: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    """Merge model-service metadata with backend ping (authoritative for UI)."""
    llm_from_model = model_payload.get("llm") if isinstance(model_payload.get("llm"), dict) else None
    if not llm_from_model:
        merged = dict(fallback)
    else:
        merged = {**llm_from_model, **fallback}
        for key in ("model", "backend", "configured", "required"):
            if llm_from_model.get(key) is not None:
                merged[key] = llm_from_model[key]

    base_url = str(merged.get("base_url") or "").strip()
    backend = str(merged.get("backend") or "")
    if base_url:
        merged["target"] = _format_target(base_url)
        merged["location"] = _llm_location(backend, base_url)
    return merged
