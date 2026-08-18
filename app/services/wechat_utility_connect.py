from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx
import websockets
from jose import jwt

from app.core.config import settings


def _ws_url_to_health_url(ws_url: str) -> str:
    normalized = ws_url.rstrip("/")
    http_base = normalized.replace("ws://", "http://", 1).replace("wss://", "https://", 1)
    return f"{http_base}/health"


def _make_wechat_jwt_token() -> str:
    secret = (settings.WECHAT_JWT_SECRET or "").strip()
    if not secret:
        raise ValueError(
            "Не задан WECHAT_JWT_SECRET в .env backend — скопируйте JWT_SECRET из .env WeChat-утилиты"
        )

    audience = (settings.WECHAT_JWT_AUDIENCE or "wechat-ws").strip()
    scope = (settings.WECHAT_JWT_SCOPE or "wechat:read").strip()
    sub = (settings.WECHAT_JWT_SUB or "avion-backend-test").strip()
    expire = datetime.now(timezone.utc) + timedelta(hours=12)

    return jwt.encode(
        {"sub": sub, "scope": scope, "aud": audience, "exp": expire},
        secret,
        algorithm="HS256",
    )


async def _fetch_health(health_url: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(health_url)
        if response.status_code >= 400:
            return None, f"HTTP {response.status_code} {response.reason_phrase} ({health_url})"
        return response.json(), None
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        return None, f"{detail} ({health_url})"


async def _connect_wechat_websocket(ws_url: str, token: str) -> dict[str, Any]:
    separator = "&" if "?" in ws_url else "?"
    uri = f"{ws_url}{separator}token={quote(token, safe='')}"

    async with asyncio.timeout(settings.WECHAT_CONNECT_TIMEOUT_SEC):
        async with websockets.connect(uri, open_timeout=settings.WECHAT_CONNECT_TIMEOUT_SEC) as ws:
            raw = await ws.recv()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            return json.loads(raw)


async def test_wechat_utility_connection() -> dict[str, Any]:
    ws_url = (settings.WECHAT_WS_URL or "ws://127.0.0.1:8790").strip()
    if not ws_url:
        return {
            "ok": False,
            "error": "Не задан WECHAT_WS_URL (например ws://192.168.5.241:8790)",
            "health": None,
            "healthError": None,
            "wsMessage": None,
            "wsUrl": None,
        }

    if not (settings.WECHAT_JWT_SECRET or "").strip():
        return {
            "ok": False,
            "error": "Не задан WECHAT_JWT_SECRET в .env backend",
            "health": None,
            "healthError": None,
            "wsMessage": None,
            "wsUrl": ws_url,
        }

    health_url = _ws_url_to_health_url(ws_url)
    health, health_error = await _fetch_health(health_url)

    try:
        token = _make_wechat_jwt_token()
        ws_message = await _connect_wechat_websocket(ws_url, token)
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        if detail in {"ConnectTimeout", "ConnectError", "TimeoutError"} or "Connect" in detail:
            detail = (
                f"{detail}: нет сетевого доступа до утилиты. "
                "Проверьте VPN/Tailscale, firewall TCP 8790 и WECHAT_WS_URL (CONNECT.md шаг 1)."
            )
        return {
            "ok": False,
            "error": detail,
            "health": health,
            "healthError": health_error,
            "wsMessage": None,
            "wsUrl": ws_url,
        }

    event = ws_message.get("event")
    if event == "hello":
        return {
            "ok": True,
            "health": health,
            "healthError": health_error,
            "wsMessage": ws_message,
            "wsUrl": ws_url,
        }

    if event == "error":
        return {
            "ok": False,
            "error": ws_message.get("error") or "Утилита вернула event=error",
            "health": health,
            "healthError": health_error,
            "wsMessage": ws_message,
            "wsUrl": ws_url,
        }

    return {
        "ok": False,
        "error": f"Неожиданный event: {event or 'unknown'}",
        "health": health,
        "healthError": health_error,
        "wsMessage": ws_message,
        "wsUrl": ws_url,
    }
