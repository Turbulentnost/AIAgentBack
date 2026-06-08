from __future__ import annotations

import os

import requests
from requests.auth import HTTPBasicAuth

DEFAULT_ONEC_BASE_URL = "http://192.168.2.229:81/erp_pm"


def normalize_odata_base(raw_base_url: str) -> str:
    base = (raw_base_url or DEFAULT_ONEC_BASE_URL).strip().rstrip("/")
    if base.endswith("/odata/standard.odata"):
        return base
    return f"{base}/odata/standard.odata"


def get_odata_base_url() -> str:
    return normalize_odata_base(os.getenv("ONEC_BASE_URL", DEFAULT_ONEC_BASE_URL))


def get_odata_auth() -> HTTPBasicAuth:
    return HTTPBasicAuth(
        os.getenv("ODATA_USER", "odata.user"),
        os.getenv("ODATA_PASSWORD", "npo852456"),
    )


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
