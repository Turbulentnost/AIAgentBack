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


def test_browser_command_is_isolated_headless_background(tmp_path: Path) -> None:
    executable = tmp_path / "browser.exe"
    executable.write_bytes(b"fake")
    provider = SystemYandexBrowserProvider(executable=executable)
    profile = str(tmp_path / "procurement-yandex-profile")
    command = provider._browser_command(profile=profile, port=9333)

    assert command[0] == str(executable)
    assert "--headless=new" in command
    assert "--dump-dom" not in command
    assert "--remote-debugging-port=9333" in command
    assert "--remote-debugging-address=127.0.0.1" in command
    assert f"--user-data-dir={profile}" in command
    assert command[-1] == "about:blank"
    assert not any("User Data" in argument for argument in command)


@pytest.mark.asyncio
async def test_search_uses_cdp_and_isolated_confirmed_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    confirmed = Path(
        r"C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe"
    )
    commands: list[tuple[str, ...]] = []

    class Process:
        returncode = 0
        pid = 4242

        async def wait(self) -> int:
            return 0

    async def create(*command: str, **_kwargs: object) -> Process:
        commands.append(command)
        return Process()

    async def fake_dom(self: object, *, port: int, url: str) -> str:
        _ = self
        assert port > 0
        assert url.startswith("https://yandex.ru/search/?text=")
        return (
            '<li class="serp-item"><a href="https://supplier.example/">'
            "Supplier</a></li>"
        )

    async def no_terminate(_process: object) -> None:
        return None

    original_is_file = Path.is_file
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda self: True if self == confirmed else original_is_file(self),
    )
    monkeypatch.setattr(providers.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(providers, "_terminate_process_tree", no_terminate)
    monkeypatch.setattr(
        SystemYandexBrowserProvider,
        "_fetch_dom_via_cdp",
        fake_dom,
    )
    provider = SystemYandexBrowserProvider(executable=confirmed, timeout_seconds=1)

    result = await provider.search("редкий подшипник", 5)

    assert result["status"] == "available"
    assert result["provider"] == "system_yandex"
    assert result["live_data"] is True
    assert commands[0][0] == str(confirmed)
    assert "--headless=new" in commands[0]
    assert "--dump-dom" not in commands[0]
    assert any(argument.startswith("--user-data-dir=") for argument in commands[0])
    assert any(
        argument.startswith("--remote-debugging-port=") for argument in commands[0]
    )
    user_data = next(
        argument for argument in commands[0] if argument.startswith("--user-data-dir=")
    )
    assert "procurement-yandex-" in user_data
    assert "YandexBrowser\\User Data" not in user_data
    assert commands[0][-1] == "about:blank"


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

    class Process:
        returncode = 0
        pid = 1001

        async def wait(self) -> int:
            return 0

    async def create(*_command: str, **_kwargs: object) -> Process:
        return Process()

    async def no_terminate(_process: object) -> None:
        return None

    async def captcha_dom(self: object, *, port: int, url: str) -> str:
        _ = (self, port, url)
        return "<html>smartcaptcha checkbox-captcha Вы не робот?</html>"

    monkeypatch.setattr(providers.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(providers, "_terminate_process_tree", no_terminate)
    monkeypatch.setattr(SystemYandexBrowserProvider, "_fetch_dom_via_cdp", captcha_dom)
    captcha = await SystemYandexBrowserProvider(executable=executable).search("q", 3)
    assert captcha["status"] == "captcha"
    assert captcha["live_data"] is False
    assert captcha["items"] == []
    assert "SmartCaptcha" in captcha["message"]

    async def hanging_dom(self: object, *, port: int, url: str) -> str:
        _ = (self, port, url)
        await providers.asyncio.sleep(10)
        return ""

    terminated: list[object] = []

    async def track_terminate(process: object) -> None:
        terminated.append(process)

    monkeypatch.setattr(SystemYandexBrowserProvider, "_fetch_dom_via_cdp", hanging_dom)
    monkeypatch.setattr(providers, "_terminate_process_tree", track_terminate)
    timed_out = await SystemYandexBrowserProvider(
        executable=executable,
        timeout_seconds=0.01,
    ).search("q", 3)
    assert timed_out["status"] == "timeout"
    assert timed_out["live_data"] is False
    assert timed_out["items"] == []
    assert terminated  # zombie browser process tree must be killed


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


@pytest.mark.asyncio
async def test_terminate_process_tree_uses_taskkill_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if providers.os.name != "nt":
        pytest.skip("Windows taskkill path")

    class Process:
        returncode = None
        pid = 7777

        def __init__(self) -> None:
            self.killed = False

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            self.returncode = -9
            return -9

    calls: list[tuple[str, ...]] = []

    class Killer:
        returncode = 0

        async def wait(self) -> int:
            return 0

    async def create(*command: str, **_kwargs: object) -> Killer:
        calls.append(command)
        return Killer()

    monkeypatch.setattr(providers.asyncio, "create_subprocess_exec", create)
    process = Process()
    await providers._terminate_process_tree(process)
    assert calls
    assert calls[0][:4] == ("taskkill", "/PID", "7777", "/T")
