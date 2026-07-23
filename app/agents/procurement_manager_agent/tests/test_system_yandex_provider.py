from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.procurement_manager_agent.supplier_mcp_server import providers
from app.agents.procurement_manager_agent.supplier_mcp_server.providers import (
    SystemYandexBrowserProvider,
    parse_yandex_results,
    resolve_yandex_browser_path,
    validate_public_url,
)


def test_resolve_yandex_browser_prefers_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "browser.exe"
    executable.write_bytes(b"test")
    monkeypatch.setenv("YANDEX_BROWSER_PATH", str(executable))
    assert resolve_yandex_browser_path() == executable


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/catalog",
        "http://127.0.0.1/catalog",
        "http://169.254.1.1/catalog",
        "http://10.0.0.1/catalog",
        "https://example.com/setup.exe",
    ],
)
def test_security_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(ValueError):
        validate_public_url(url)


def test_parse_yandex_results() -> None:
    document = """
    <ul>
      <li class="serp-item">
        <a class="OrganicTitle-Link" href="https://supplier.example/catalog">Сталь оптом</a>
        <div class="OrganicTextContentSpan">Поставщик промышленной стали.</div>
      </li>
      <li class="serp-item">
        <a href="https://second.example/">Комплектующие</a>
        <span class="organic__text">Каталог комплектующих</span>
      </li>
    </ul>
    """
    assert parse_yandex_results(document, 10) == [
        {
            "title": "Сталь оптом",
            "url": "https://supplier.example/catalog",
            "snippet": "Поставщик промышленной стали.",
        },
        {
            "title": "Комплектующие",
            "url": "https://second.example/",
            "snippet": "Каталог комплектующих",
        },
    ]


@pytest.mark.asyncio
async def test_search_uses_yandex_and_isolated_confirmed_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    confirmed = Path(
        r"C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe"
    )
    commands: list[tuple[str, ...]] = []

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (
                b'<li class="serp-item"><a href="https://supplier.example/">'
                b"Supplier</a></li>",
                b"",
            )

    async def create(*command: str, **_kwargs: object) -> Process:
        commands.append(command)
        return Process()

    original_is_file = Path.is_file
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda self: True if self == confirmed else original_is_file(self),
    )
    monkeypatch.setattr(providers.asyncio, "create_subprocess_exec", create)
    provider = SystemYandexBrowserProvider(executable=confirmed, timeout_seconds=1)

    result = await provider.search("редкий подшипник", 5)

    assert result["status"] == "available"
    assert result["provider"] == "system_yandex"
    assert result["live_data"] is True
    assert commands[0][0] == str(confirmed)
    assert "--headless" in commands[0]
    assert "--dump-dom" in commands[0]
    assert any(argument.startswith("--user-data-dir=") for argument in commands[0])
    assert commands[0][-1].startswith("https://yandex.ru/search/?text=")
    assert "duckduckgo" not in commands[0][-1].casefold()


@pytest.mark.asyncio
async def test_missing_executable_is_explicitly_unavailable(tmp_path: Path) -> None:
    provider = SystemYandexBrowserProvider(executable=tmp_path / "missing.exe")
    result = await provider.search("steel", 5)
    assert result["status"] == "unavailable"
    assert result["live_data"] is False
    assert result["items"] == []


@pytest.mark.asyncio
async def test_captcha_and_timeout_statuses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "browser.exe"
    executable.write_bytes(b"fake")

    class CaptchaProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"<html>showcaptcha robot check</html>", b"")

    async def create_captcha(*_command: str, **_kwargs: object) -> CaptchaProcess:
        return CaptchaProcess()

    monkeypatch.setattr(providers.asyncio, "create_subprocess_exec", create_captcha)
    captcha = await SystemYandexBrowserProvider(executable=executable).search("q", 3)
    assert captcha["status"] == "captcha"
    assert captcha["live_data"] is False
    assert captcha["items"] == []

    class HangingProcess:
        returncode = None

        async def communicate(self) -> tuple[bytes, bytes]:
            await providers.asyncio.sleep(10)
            return (b"", b"")

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return -9

    async def create_hang(*_command: str, **_kwargs: object) -> HangingProcess:
        return HangingProcess()

    monkeypatch.setattr(providers.asyncio, "create_subprocess_exec", create_hang)
    timed_out = await SystemYandexBrowserProvider(
        executable=executable,
        timeout_seconds=0.01,
    ).search("q", 3)
    assert timed_out["status"] == "timeout"
    assert timed_out["live_data"] is False
    assert timed_out["items"] == []


@pytest.mark.asyncio
async def test_fetch_rejects_ssrf_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "browser.exe"
    executable.write_bytes(b"fake")
    calls = 0

    async def create(*_command: str, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("subprocess must not start for blocked URLs")

    monkeypatch.setattr(providers.asyncio, "create_subprocess_exec", create)
    provider = SystemYandexBrowserProvider(executable=executable)
    result = await provider.fetch("http://127.0.0.1/secret")
    assert result["status"] == "unavailable"
    assert calls == 0
