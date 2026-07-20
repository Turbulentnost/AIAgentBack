from __future__ import annotations

import os

import requests
from requests.auth import HTTPBasicAuth

from app.core.config import settings

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
