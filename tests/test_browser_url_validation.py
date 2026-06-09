from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.browser_runner_service import BrowserRunnerError, BrowserRunnerService


def test_browser_url_validation_allows_allowlisted_domain(monkeypatch) -> None:
    monkeypatch.setattr(settings, "BROWSER_ALLOWED_DOMAINS", "portal.company.local,*.corp.local")
    service = BrowserRunnerService(db=None)  # type: ignore[arg-type]

    service.validate_url("https://portal.company.local/tasks/123")
    service.validate_url("https://docs.corp.local/page")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://localhost/admin",
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "https://evil.example/download.exe",
    ],
)
def test_browser_url_validation_blocks_unsafe_targets(monkeypatch, url: str) -> None:
    monkeypatch.setattr(settings, "BROWSER_ALLOWED_DOMAINS", "evil.example,portal.company.local")
    service = BrowserRunnerService(db=None)  # type: ignore[arg-type]

    with pytest.raises(BrowserRunnerError):
        service.validate_url(url)


def test_open_web_policy_allows_any_public_domain(monkeypatch) -> None:
    # allowlist intentionally narrow; open-web must bypass it for public hosts.
    monkeypatch.setattr(settings, "BROWSER_ALLOWED_DOMAINS", "portal.company.local")
    service = BrowserRunnerService(db=None)  # type: ignore[arg-type]

    with pytest.raises(BrowserRunnerError):
        service.validate_url("https://gismeteo.ru/weather")

    service.validate_url("https://gismeteo.ru/weather", allow_any_domain=True)
    service.validate_url("https://html.duckduckgo.com/html/?q=weather", allow_any_domain=True)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://127.0.0.1/admin",
        "http://10.0.0.5/internal",
        "http://169.254.169.254/latest/meta-data",
        "javascript:alert(1)",
        "https://evil.example/payload.exe",
    ],
)
def test_open_web_policy_still_blocks_internal_and_unsafe(url: str) -> None:
    service = BrowserRunnerService(db=None)  # type: ignore[arg-type]

    with pytest.raises(BrowserRunnerError):
        service.validate_url(url, allow_any_domain=True)
