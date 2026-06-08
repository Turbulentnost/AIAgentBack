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
