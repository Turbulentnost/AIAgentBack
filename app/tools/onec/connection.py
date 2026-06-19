from __future__ import annotations

from dataclasses import dataclass

import requests
from requests.auth import HTTPBasicAuth

from app.core.config import settings
from app.integrations.onec_odata import normalize_odata_base


@dataclass(frozen=True)
class ODataConfig:
    url: str
    user: str
    password: str
    timeout: int = 120


def build_odata_config() -> ODataConfig:
    return ODataConfig(
        url=normalize_odata_base(settings.ONEC_ODATA_URL),
        user=settings.ONEC_ODATA_USER,
        password=settings.ONEC_ODATA_PASSWORD,
        timeout=settings.ONEC_ODATA_TIMEOUT,
    )


CONFIG = build_odata_config()


def create_session(config: ODataConfig = CONFIG) -> requests.Session:
    session = requests.Session()
    session.auth = HTTPBasicAuth(config.user, config.password)
    return session
