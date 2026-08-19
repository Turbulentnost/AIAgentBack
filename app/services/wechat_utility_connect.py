from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import httpx
import websockets
from jose import jwt

from app.core.config import settings

_MEDIA_TYPES = {"file", "image", "audio", "video", "voice", "pic", "picture", "img", "ptt"}
_media_get_template: str | None = None


def log_wechat_payload_to_console(payload: dict[str, Any], source: str = "listener") -> None:
    event = str(payload.get("event") or "")
    if event == "hello":
        remember_media_get_template(payload)
        line = (
            f"[WeChat {source}] hello clients={payload.get('clients')} "
            f"time={payload.get('time')} mediaGet={payload.get('mediaGet') or payload.get('media')}"
        )
    elif event == "error":
        line = f"[WeChat {source}] error {payload.get('error')}"
    else:
        file_name = None
        file_payload = payload.get("file")
        if isinstance(file_payload, dict):
            file_name = file_payload.get("name") or file_payload.get("url")
        line = (
            f"[WeChat {source}] message time={payload.get('time')} "
            f"group={payload.get('group')!r} sender={payload.get('sender')!r} "
            f"type={payload.get('type')} text={payload.get('text')!r} "
            f"file={file_name!r} hasFile={payload.get('hasFile')}"
        )
    print(line, flush=True)


def _ws_url_to_health_url(ws_url: str) -> str:
    return f"{wechat_utility_http_base(ws_url)}/health"


def wechat_utility_http_base(ws_url: str | None = None) -> str:
    raw = (ws_url or settings.WECHAT_WS_URL or "ws://192.168.5.80:8790").strip().rstrip("/")
    return raw.replace("ws://", "http://", 1).replace("wss://", "https://", 1)


def remember_media_get_template(payload: dict[str, Any]) -> None:
    global _media_get_template
    raw = str(payload.get("mediaGet") or payload.get("media") or "").strip()
    if raw:
        _media_get_template = raw


def media_filename_candidates(payload: dict[str, Any] | None, file_payload: dict[str, Any] | None = None) -> list[str]:
    extra = payload or {}
    file_obj = file_payload or (extra.get("file") if isinstance(extra.get("file"), dict) else {}) or {}
    names: list[str] = []
    for value in (file_obj.get("name"), extra.get("fileName"), extra.get("file_name")):
        text = str(value or "").strip()
        if text:
            names.append(text)

    msg_type = str(extra.get("type") or "").strip().lower()
    text = str(extra.get("text") or "").strip()
    if text and Path(text).suffix:
        names.append(text)

    msg_id = str(extra.get("id") or extra.get("externalId") or "").strip()
    if msg_type in {"image", "pic", "picture", "img"}:
        if msg_id:
            names.extend([f"{msg_id}.jpg", f"{msg_id}.png", msg_id])

    unique: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique[:8]


def build_wechat_media_url(file_name: str) -> str | None:
    name = str(file_name or "").strip()
    if not name:
        return None
    encoded = quote(name, safe="")
    template = (_media_get_template or "").split("?token=", 1)[0]
    if "<fileName>" in template:
        candidate = template.replace("<fileName>", encoded)
    else:
        candidate = f"{wechat_utility_http_base()}/media/{encoded}"
    if not _same_utility_host(candidate, wechat_utility_http_base()):
        return None
    return candidate


def _same_utility_host(candidate: str, allowed_base: str) -> bool:
    parsed = urlparse(candidate)
    allowed = urlparse(allowed_base)
    if parsed.scheme not in {"http", "https"}:
        return False
    if (parsed.hostname or "").lower() != (allowed.hostname or "").lower():
        return False
    candidate_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    allowed_port = allowed.port or (443 if allowed.scheme == "https" else 80)
    return candidate_port == allowed_port


def resolve_wechat_media_url(file_payload: dict[str, Any], payload: dict[str, Any] | None = None) -> str | None:
    extra = payload or {}
    url = str(file_payload.get("url") or extra.get("url") or "").strip()
    path = str(file_payload.get("path") or extra.get("path") or "").strip()
    name = str(file_payload.get("name") or extra.get("fileName") or "").strip()
    base = wechat_utility_http_base()

    if url.startswith(("http://", "https://")):
        candidate = url
    elif path.startswith(("http://", "https://")):
        candidate = path
    elif path.startswith("/"):
        candidate = f"{base}{path}"
    elif name:
        candidate = build_wechat_media_url(name)
        return candidate
    else:
        first = next(iter(media_filename_candidates(extra, file_payload)), None)
        return build_wechat_media_url(first) if first else None

    if not _same_utility_host(candidate, base):
        return None
    return candidate


def _url_with_token(url: str, token: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["token"] = token
    return urlunparse(parsed._replace(query=urlencode(query)))


def _headers_for_log(headers: Any) -> dict[str, str]:
    return {str(key): str(value) for key, value in dict(headers).items()}


def _print_media_log(title: str, payload: dict[str, Any]) -> None:
    print(f"[WeChat media] {title} {json.dumps(payload, ensure_ascii=False)}", flush=True)


async def download_wechat_media_file(url: str) -> tuple[bytes | None, str | None, dict[str, Any]]:
    """GET /media/... с JWT: query token + Authorization Bearer."""
    request_log: dict[str, Any] = {
        "method": "GET",
        "url": url,
        "headers": {},
    }
    download_log: dict[str, Any] = {"event": "media-download", "request": request_log, "response": None}

    try:
        token = _make_wechat_jwt_token()
    except ValueError as exc:
        download_log["response"] = {"ok": False, "error": str(exc)}
        _print_media_log("запрос не отправлен", download_log)
        return None, str(exc), download_log

    request_url = _url_with_token(url, token)
    request_log["url"] = request_url
    request_log["headers"] = {"Authorization": f"Bearer {token}"}
    _print_media_log("отправляется запрос", request_log)

    timeout = httpx.Timeout(30.0, connect=min(10.0, settings.WECHAT_CONNECT_TIMEOUT_SEC))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.get(request_url, headers={"Authorization": f"Bearer {token}"})
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        error = f"не удалось скачать {url}: {detail}"
        download_log["response"] = {"ok": False, "error": error}
        _print_media_log("ответ / ошибка", download_log["response"])
        return None, error, download_log

    content_type = response.headers.get("content-type") or ""
    body_preview = None
    if "json" in content_type or content_type.startswith("text/"):
        body_preview = (response.text or "")[:500]
    response_log = {
        "ok": response.status_code < 400,
        "status": response.status_code,
        "reason": response.reason_phrase,
        "headers": _headers_for_log(response.headers),
        "bytes": len(response.content),
        "contentType": content_type or None,
        "bodyPreview": body_preview,
    }
    download_log["response"] = response_log
    _print_media_log("ответ", response_log)

    if response.status_code >= 400:
        return None, f"не удалось скачать {url}: HTTP {response.status_code}", download_log

    data = response.content
    if not data:
        error = f"пустой ответ при скачивании {url}"
        response_log["error"] = error
        return None, error, download_log
    if len(data) > 50 * 1024 * 1024:
        error = f"файл слишком большой ({len(data)} байт): {url}"
        response_log["error"] = error
        return None, error, download_log
    return data, None, download_log


async def download_wechat_media_candidates(
    file_payload: dict[str, Any],
    payload: dict[str, Any] | None = None,
    retries: int = 4,
) -> tuple[bytes | None, str | None, dict[str, Any], str | None]:
    """Пробует file.url и имена из text/fileName/id, при 404 повторяет — файл на утилите появляется с задержкой."""
    extra = payload or {}
    urls: list[str] = []
    direct = resolve_wechat_media_url(file_payload, extra)
    if direct:
        urls.append(direct)
    for name in media_filename_candidates(extra, file_payload):
        built = build_wechat_media_url(name)
        if built and built not in urls:
            urls.append(built)

    download_log: dict[str, Any] = {
        "event": "media-download",
        "request": {"method": "GET", "urls": list(urls)},
        "response": None,
    }
    if not urls:
        error = "нет имени файла и file.url для GET /media/<fileName>"
        download_log["response"] = {"ok": False, "error": error}
        _print_media_log("запрос не отправлен", download_log)
        return None, error, download_log, None

    last_error = "файл не найден на утилите"
    last_log = download_log
    retries = max(1, retries)
    delay_sec = 2.0
    for attempt in range(retries):
        for url in urls:
            data, error, log = await download_wechat_media_file(url)
            last_error = error or last_error
            last_log = log
            if data is not None:
                last_log["request"] = {**(log.get("request") or {}), "attempt": attempt + 1}
                return data, None, last_log, url
        if attempt + 1 < retries:
            print(
                f"[WeChat media] файл ещё не готов, повтор {attempt + 1}/{retries - 1} через {delay_sec:.0f}с",
                flush=True,
            )
            await asyncio.sleep(delay_sec)

    last_log["response"] = last_log.get("response") or {"ok": False, "error": last_error}
    return None, last_error, last_log, None


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


def _wechat_ws_uri(ws_url: str, token: str) -> str:
    separator = "&" if "?" in ws_url else "?"
    return f"{ws_url}{separator}token={quote(token, safe='')}"


async def _connect_wechat_websocket(ws_url: str, token: str) -> dict[str, Any]:
    uri = _wechat_ws_uri(ws_url, token)

    async with asyncio.timeout(settings.WECHAT_CONNECT_TIMEOUT_SEC):
        async with websockets.connect(uri, open_timeout=settings.WECHAT_CONNECT_TIMEOUT_SEC) as ws:
            raw = await ws.recv()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            return json.loads(raw)


async def create_wechat_session() -> dict[str, Any]:
    ws_url = (settings.WECHAT_WS_URL or "ws://192.168.5.80:8790").strip()
    if not ws_url:
        return {
            "ok": False,
            "error": "Не задан WECHAT_WS_URL (например ws://192.168.5.80:8790)",
            "health": None,
            "healthError": None,
            "wsUrl": None,
            "token": None,
        }

    health_url = _ws_url_to_health_url(ws_url)
    health, health_error = await _fetch_health(health_url)

    if not (settings.WECHAT_JWT_SECRET or "").strip():
        return {
            "ok": False,
            "error": "Не задан WECHAT_JWT_SECRET в .env backend",
            "health": health,
            "healthError": health_error,
            "wsUrl": ws_url,
            "token": None,
        }

    return {
        "ok": True,
        "health": health,
        "healthError": health_error,
        "wsUrl": ws_url,
        "token": _make_wechat_jwt_token(),
    }


async def stream_wechat_utility_messages():
    session = await create_wechat_session()
    if not session.get("ok") or not session.get("wsUrl") or not session.get("token"):
        raise RuntimeError(session.get("error") or "Не удалось создать сессию WeChat-утилиты")

    from app.services.wechat_message_store import persist_wechat_payload

    uri = _wechat_ws_uri(str(session["wsUrl"]), str(session["token"]))
    async with websockets.connect(uri, open_timeout=settings.WECHAT_CONNECT_TIMEOUT_SEC) as ws:
        async for raw in ws:
            download_log: dict[str, Any] | None = None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                log_wechat_payload_to_console(payload, source="stream")
                try:
                    _row, download_log = await persist_wechat_payload(payload)
                except Exception:
                    download_log = None
            yield raw
            if download_log:
                yield json.dumps(download_log, ensure_ascii=False)


_listener_state: dict[str, Any] = {
    "running": False,
    "connected": False,
    "wsUrl": None,
    "lastHello": None,
    "lastMessageAt": None,
    "lastError": None,
    "lastFrame": None,
    "reconnects": 0,
    "hint": None,
}


def wechat_listener_status() -> dict[str, Any]:
    return dict(_listener_state)


async def _wait_or_stop(stop: asyncio.Event, timeout: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), timeout=timeout)
        return True
    except TimeoutError:
        return False


async def wechat_history_listener_loop(stop: asyncio.Event) -> None:
    """Постоянный WebSocket к утилите с момента старта backend. Не останавливается сам."""
    from app.core.logging import get_logger
    from app.services.wechat_message_store import ensure_wechat_tables, persist_wechat_payload

    logger = get_logger(__name__)
    await ensure_wechat_tables()
    _listener_state["running"] = True
    delay = 1.0
    logger.info("wechat.listener.started", url=(settings.WECHAT_WS_URL or "").strip())

    while not stop.is_set():
        session: dict[str, Any] | None = None
        try:
            session = await create_wechat_session()
            if not session.get("ok") or not session.get("wsUrl") or not session.get("token"):
                error = session.get("error") or "сессия утилиты не готова"
                _listener_state.update({"connected": False, "lastError": error, "wsUrl": session.get("wsUrl")})
                logger.warning("wechat.listener.session_failed", error=error)
                if await _wait_or_stop(stop, delay):
                    break
                delay = min(delay * 2, 15)
                continue

            uri = _wechat_ws_uri(str(session["wsUrl"]), str(session["token"]))
            async with websockets.connect(
                uri,
                open_timeout=settings.WECHAT_CONNECT_TIMEOUT_SEC,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
            ) as ws:
                delay = 1.0
                _listener_state.update(
                    {
                        "connected": True,
                        "wsUrl": session["wsUrl"],
                        "lastError": None,
                    }
                )
                logger.info("wechat.listener.connected", url=session["wsUrl"])
                try:
                    from app.services.wechat_message_store import backfill_missing_wechat_files

                    await backfill_missing_wechat_files()
                except Exception as backfill_exc:
                    logger.warning("wechat.listener.backfill_failed", error=str(backfill_exc))
                last_message_mono = asyncio.get_running_loop().time()
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=15)
                    except TimeoutError:
                        idle_for = asyncio.get_running_loop().time() - last_message_mono
                        if idle_for > 90:
                            _listener_state["hint"] = (
                                "Сокет жив, но утилита не шлёт message. "
                                "Проверьте GROUP_ALLOWLIST=НПО оборудование, SKIP_SELF и что Ferry/WeChat на 192.168.5.80 живы."
                            )
                            logger.warning("wechat.listener.idle_reconnect", idle_sec=round(idle_for))
                            break
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    _listener_state["lastFrame"] = raw[:500]
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    event = str(payload.get("event") or "")
                    log_wechat_payload_to_console(payload, source="listener")
                    if event == "hello":
                        _listener_state["lastHello"] = payload.get("time") or datetime.now(timezone.utc).isoformat()
                        continue
                    if event == "error":
                        _listener_state["lastError"] = str(payload.get("error") or "утилита вернула error")
                        logger.warning("wechat.listener.utility_error", error=_listener_state["lastError"])
                        continue
                    last_message_mono = asyncio.get_running_loop().time()
                    _listener_state["lastMessageAt"] = payload.get("time") or datetime.now(timezone.utc).isoformat()
                    _listener_state["hint"] = None
                    try:
                        await persist_wechat_payload(payload)
                    except Exception as persist_exc:
                        logger.warning("wechat.listener.persist_failed", error=str(persist_exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _listener_state["connected"] = False
            _listener_state["lastError"] = str(exc).strip() or type(exc).__name__
            _listener_state["reconnects"] = int(_listener_state.get("reconnects") or 0) + 1
            logger.warning("wechat.listener.disconnected", error=_listener_state["lastError"])
            if await _wait_or_stop(stop, delay):
                break
            delay = min(delay * 2, 15)
            continue

        _listener_state["connected"] = False
        _listener_state["reconnects"] = int(_listener_state.get("reconnects") or 0) + 1
        if stop.is_set():
            break
        logger.warning("wechat.listener.closed", hint="reconnect")
        if await _wait_or_stop(stop, delay):
            break
        delay = min(delay * 2, 15)

    _listener_state["running"] = False
    _listener_state["connected"] = False
    logger.info("wechat.listener.stopped")


async def test_wechat_utility_connection() -> dict[str, Any]:
    ws_url = (settings.WECHAT_WS_URL or "ws://192.168.5.80:8790").strip()
    if not ws_url:
        return {
            "ok": False,
            "error": "Не задан WECHAT_WS_URL (например ws://192.168.5.80:8790)",
            "health": None,
            "healthError": None,
            "wsMessage": None,
            "wsUrl": None,
        }

    health_url = _ws_url_to_health_url(ws_url)
    health, health_error = await _fetch_health(health_url)

    if not (settings.WECHAT_JWT_SECRET or "").strip():
        return {
            "ok": False,
            "error": (
                "Сеть до утилиты проверена через /health, но WECHAT_JWT_SECRET не задан. "
                "Скопируйте JWT_SECRET из .env WeChat-утилиты в AIAgentBack/.env и перезапустите backend."
            ),
            "health": health,
            "healthError": health_error,
            "wsMessage": None,
            "wsUrl": ws_url,
        }

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
