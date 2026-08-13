from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import requests
from requests.auth import HTTPBasicAuth
from urllib3.exceptions import MaxRetryError

from requests.exceptions import ConnectTimeout, ConnectionError as RequestsConnectionError, RequestException

from app.core.config import settings

DEFAULT_READ_TIMEOUT_SEC = 120
DEFAULT_CONNECT_RETRIES = 3
DEFAULT_CONNECT_RETRY_DELAY_SEC = 2.0

DEFAULT_ONEC_BASE_URL = "http://192.168.2.229:81/erp_pm"


def normalize_odata_base(raw_base_url: str) -> str:
    base = (raw_base_url or DEFAULT_ONEC_BASE_URL).strip().rstrip("/")
    if base.endswith("/odata/standard.odata"):
        return base
    return f"{base}/odata/standard.odata"


def get_odata_base_url() -> str:
    raw_url = settings.ONEC_ODATA_URL or os.getenv("ONEC_BASE_URL", DEFAULT_ONEC_BASE_URL)
    return normalize_odata_base(raw_url)


def get_odata_auth() -> HTTPBasicAuth:
    user = settings.ONEC_ODATA_USER or os.getenv("ODATA_USER", "odata.user")
    password = settings.ONEC_ODATA_PASSWORD or os.getenv("ODATA_PASSWORD", "")
    return HTTPBasicAuth(user, password)


def create_session() -> requests.Session:
    session = requests.Session()
    session.auth = get_odata_auth()
    return session


def get_request_timeout() -> tuple[float, float]:
    """(connect_timeout, read_timeout) — connect из ONEC_ODATA_TIMEOUT, read для тяжёлых OData."""
    connect = float(settings.ONEC_ODATA_TIMEOUT or 15)
    return connect, float(DEFAULT_READ_TIMEOUT_SEC)


def format_onec_request_error(exc: Exception, *, base_url: str = "") -> str:
    host = urlparse(base_url or get_odata_base_url()).hostname or "1С"
    if isinstance(exc, ConnectTimeout):
        return (
            f"Сервер 1С ({host}) не отвечает — проверьте VPN, сеть или доступность публикации OData."
        )
    if isinstance(exc, MaxRetryError):
        return (
            f"Сервер 1С ({host}) не отвечает — проверьте VPN, сеть или доступность публикации OData."
        )
    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, (ConnectTimeout, MaxRetryError)):
        return (
            f"Сервер 1С ({host}) не отвечает — проверьте VPN, сеть или доступность публикации OData."
        )
    if isinstance(exc, RequestsConnectionError):
        return f"Нет соединения с сервером 1С ({host}). Проверьте сеть и адрес ONEC_ODATA_URL."
    if isinstance(exc, RequestException):
        return f"Ошибка запроса к 1С ({host}): {exc}"
    return str(exc)


def get_json(
    session: requests.Session,
    url: str,
    *,
    retries: int = DEFAULT_CONNECT_RETRIES,
    retry_delay_sec: float = DEFAULT_CONNECT_RETRY_DELAY_SEC,
) -> dict:
    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            response = session.get(
                url,
                timeout=get_request_timeout(),
                headers={"Accept": "application/json"},
            )
            response.encoding = "utf-8"
            if not response.ok:
                raise RuntimeError(f"HTTP {response.status_code}: {(response.text or '')[:500]}")
            return response.json()
        except (ConnectTimeout, RequestsConnectionError) as exc:
            last_exc = exc
            if attempt + 1 >= retries:
                raise
            time.sleep(retry_delay_sec)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Не удалось выполнить запрос к 1С OData")


def fetch_all(
    session: requests.Session,
    url: str,
    page: int = 1000,
    timeout: int = 120,
) -> list[dict]:
    rows: list[dict] = []
    skip = 0
    while True:
        sep = "&" if "?" in url else "?"
        page_url = f"{url}{sep}$top={page}&$skip={skip}"
        response = session.get(page_url, timeout=timeout)
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")

        batch = response.json().get("value", [])
        if not batch:
            break

        rows.extend(batch)
        if len(batch) < page:
            break
        skip += len(batch)

    return rows
