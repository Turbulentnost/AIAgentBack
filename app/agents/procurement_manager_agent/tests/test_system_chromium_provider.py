from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.procurement_manager_agent.supplier_mcp_server import providers
from app.agents.procurement_manager_agent.supplier_mcp_server.providers import (
    FallbackBrowserSearchProvider,
    SystemChromiumWebSearchProvider,
    SystemYandexBrowserProvider,
    build_default_browser_search_provider,
    parse_bing_results,
    parse_duckduckgo_results,
    resolve_system_browser_path,
)


def test_resolve_system_browser_prefers_env_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "msedge.exe"
    executable.write_bytes(b"test")
    monkeypatch.setenv("PROCUREMENT_WEB_BROWSER_PATH", str(executable))
    assert resolve_system_browser_path() == executable


def test_resolve_system_browser_respects_prefer_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chrome = tmp_path / "chrome.exe"
    edge = tmp_path / "msedge.exe"
    chrome.write_bytes(b"c")
    edge.write_bytes(b"e")
    monkeypatch.delenv("PROCUREMENT_WEB_BROWSER_PATH", raising=False)
    monkeypatch.setenv("PROCUREMENT_WEB_BROWSER_PREFER", "chrome")
    monkeypatch.setattr(
        providers,
        "DEFAULT_CHROME_PATHS",
        (chrome,),
    )
    monkeypatch.setattr(
        providers,
        "DEFAULT_EDGE_PATHS",
        (edge,),
    )
    monkeypatch.setattr(providers, "DEFAULT_CHROMIUM_PATHS", ())
    assert resolve_system_browser_path() == chrome
    monkeypatch.setenv("PROCUREMENT_WEB_BROWSER_PREFER", "edge")
    assert resolve_system_browser_path() == edge


def test_parse_duckduckgo_results() -> None:
    document = """
    <div class="result results_links web-result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fsteel.example%2F">
        Сталь оптом
      </a>
      <a class="result__snippet" href="#">Поставщик металлопроката</a>
    </div>
    <div class="result web-result">
      <a rel="nofollow" class="result__a" href="https://bearing.example/catalog">
        Подшипники
      </a>
    </div>
    <div class="result web-result">
      <a class="result__a" href="https://duckduckgo.com/about">internal</a>
    </div>
    """
    assert parse_duckduckgo_results(document, 10) == [
        {
            "title": "Сталь оптом",
            "url": "https://steel.example/",
            "snippet": "Поставщик металлопроката",
        },
        {
            "title": "Подшипники",
            "url": "https://bearing.example/catalog",
            "snippet": "",
        },
    ]


def test_parse_bing_results_unwraps_ck_redirect() -> None:
    # u=a1 + base64("https://supplier.example/catalog")
    encoded = "a1aHR0cHM6Ly9zdXBwbGllci5leGFtcGxlL2NhdGFsb2c"
    document = f"""
    <ol id="b_results">
      <li class="b_algo">
        <cite>https://supplier.example › catalog</cite>
        <h2><a href="https://www.bing.com/ck/a?!&&amp;u={encoded}&amp;ntb=1">Металлопрокат</a></h2>
        <p class="b_lineclamp2">Каталог стали и труб</p>
      </li>
      <li class="b_algo">
        <h2><a href="https://bing.com/maps">Maps</a></h2>
        <p>engine</p>
      </li>
    </ol>
    """
    assert parse_bing_results(document, 5) == [
        {
            "title": "Металлопрокат",
            "url": "https://supplier.example/catalog",
            "snippet": "Каталог стали и труб",
        }
    ]


def test_chromium_command_is_isolated_headless(tmp_path: Path) -> None:
    executable = tmp_path / "msedge.exe"
    executable.write_bytes(b"fake")
    provider = SystemChromiumWebSearchProvider(executable=executable)
    profile = str(tmp_path / "procurement-chromium-profile")
    command = provider._browser_command(profile=profile, port=9444)
    assert command[0] == str(executable)
    assert "--headless=new" in command
    assert f"--user-data-dir={profile}" in command
    assert not any("User Data" in argument for argument in command)
    assert command[-1] == "about:blank"


@pytest.mark.asyncio
async def test_chromium_search_uses_ddg_and_maps_items(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "msedge.exe"
    executable.write_bytes(b"fake")
    commands: list[tuple[str, ...]] = []

    class Process:
        returncode = 0
        pid = 5150

        async def wait(self) -> int:
            return 0

    async def create(*command: str, **_kwargs: object) -> Process:
        commands.append(command)
        return Process()

    async def fake_dom(self: object, *, port: int, url: str) -> str:
        _ = (self, port)
        assert "lite.duckduckgo.com/lite/" in url or "html.duckduckgo.com/html/" in url
        if "lite.duckduckgo.com" in url:
            return (
                '<a rel="nofollow" href="https://supplier.example/steel">Steel Co</a>'
            )
        return (
            '<div class="result web-result">'
            '<a class="result__a" href="https://supplier.example/steel">Steel Co</a>'
            '<a class="result__snippet">Industrial steel</a>'
            "</div>"
        )

    async def no_terminate(_process: object) -> None:
        return None

    monkeypatch.setattr(providers.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(providers, "_terminate_process_tree", no_terminate)
    monkeypatch.setattr(
        SystemChromiumWebSearchProvider,
        "_fetch_dom_via_cdp",
        fake_dom,
    )
    provider = SystemChromiumWebSearchProvider(
        executable=executable,
        search_engine="duckduckgo",
        timeout_seconds=1,
    )
    result = await provider.search("поставщик стали", 5)
    assert result["status"] == "available"
    assert result["provider"] == "system_chromium"
    assert result["search_engine"] == "duckduckgo"
    assert result["live_data"] is True
    assert result["items"][0]["title"] == "Steel Co"
    assert result["items"][0]["url"] == "https://supplier.example/steel"


@pytest.mark.asyncio
async def test_chromium_bing_search_with_ua_override_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "msedge.exe"
    executable.write_bytes(b"fake")

    class Process:
        returncode = 0
        pid = 6161

        async def wait(self) -> int:
            return 0

    commands: list[tuple[str, ...]] = []

    async def create(*command: str, **_kwargs: object) -> Process:
        commands.append(command)
        return Process()

    async def fake_dom(self: object, *, port: int, url: str) -> str:
        _ = (self, port)
        assert "bing.com/search" in url
        assert "setlang=ru-RU" in url
        encoded = "a1aHR0cHM6Ly9tZXRhbGxvdG9yZy5ydS8"
        return f"""
        <li class="b_algo">
          <h2><a href="https://www.bing.com/ck/a?!&&amp;u={encoded}&amp;ntb=1">
            Металлоторг
          </a></h2>
          <p class="b_lineclamp2">Поставщик металлопроката</p>
        </li>
        """

    async def no_terminate(_process: object) -> None:
        return None

    monkeypatch.setattr(providers.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(providers, "_terminate_process_tree", no_terminate)
    monkeypatch.setattr(SystemChromiumWebSearchProvider, "_fetch_dom_via_cdp", fake_dom)
    provider = SystemChromiumWebSearchProvider(
        executable=executable,
        search_engine="bing",
        timeout_seconds=1,
    )
    assert "Edg/" in provider._headless_user_agent()
    result = await provider.search("сталь", 3)
    assert result["status"] == "available"
    assert result["search_engine"] == "bing"
    assert result["items"][0]["url"] == "https://metallotorg.ru/"
    user_data = next(
        argument for argument in commands[0] if argument.startswith("--user-data-dir=")
    )
    assert "procurement-chromium-" in user_data
    assert "Edge\\User Data" not in user_data
    assert "Chrome\\User Data" not in user_data


@pytest.mark.asyncio
async def test_fallback_uses_chromium_after_yandex_captcha() -> None:
    class Yandex:
        async def search(self, query: str, limit: int) -> dict:
            _ = (query, limit)
            return {
                "status": "captcha",
                "provider": "system_yandex",
                "items": [],
                "live_data": False,
                "message": "Yandex SmartCaptcha blocked headless search",
            }

        async def fetch(self, url: str) -> dict:
            return {"status": "unavailable", "url": url}

    class Chromium:
        async def search(self, query: str, limit: int) -> dict:
            _ = limit
            return {
                "status": "available",
                "provider": "system_chromium",
                "query": query,
                "items": [
                    {
                        "title": "Real Supplier",
                        "url": "https://real.example/",
                        "snippet": "live",
                    }
                ],
                "live_data": True,
                "search_engine": "duckduckgo",
            }

        async def fetch(self, url: str) -> dict:
            return {"status": "available", "url": url, "live_data": True}

    provider = FallbackBrowserSearchProvider(primary=Yandex(), fallback=Chromium())
    result = await provider.search("steel", 3)
    assert result["status"] == "available"
    assert result["provider"] == "system_chromium"
    assert result["fallback_used"] is True
    assert result["primary_status"] == "captcha"
    assert result["items"][0]["url"] == "https://real.example/"
    assert result["live_data"] is True


@pytest.mark.asyncio
async def test_fallback_does_not_fabricate_when_both_fail() -> None:
    class Dead:
        def __init__(self, name: str) -> None:
            self.name = name

        async def search(self, query: str, limit: int) -> dict:
            _ = (query, limit)
            return {
                "status": "unavailable",
                "provider": self.name,
                "items": [],
                "live_data": False,
                "message": f"{self.name} down",
            }

        async def fetch(self, url: str) -> dict:
            return {"status": "unavailable", "url": url, "provider": self.name}

    provider = FallbackBrowserSearchProvider(
        primary=Dead("system_yandex"),
        fallback=Dead("system_chromium"),
    )
    result = await provider.search("q", 2)
    assert result["status"] == "unavailable"
    assert result["items"] == []
    assert result["live_data"] is False
    assert result["fallback_used"] is True
    assert "system_yandex" in result["message"]
    assert "system_chromium" in result["message"]


def test_build_default_prefers_chromium_when_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    edge = tmp_path / "msedge.exe"
    edge.write_bytes(b"e")
    monkeypatch.setenv("PROCUREMENT_WEB_BROWSER_PATH", str(edge))
    monkeypatch.setenv("PROCUREMENT_WEB_SEARCH_PROVIDER", "auto")
    monkeypatch.delenv("YANDEX_BROWSER_PATH", raising=False)
    provider = build_default_browser_search_provider()
    assert isinstance(provider, FallbackBrowserSearchProvider)
    assert isinstance(provider.primary, SystemChromiumWebSearchProvider)
    assert isinstance(provider.fallback, SystemYandexBrowserProvider)
    assert provider.primary.executable == edge


def test_build_default_yandex_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCUREMENT_WEB_SEARCH_PROVIDER", "yandex")
    provider = build_default_browser_search_provider()
    assert isinstance(provider, SystemYandexBrowserProvider)


@pytest.mark.asyncio
async def test_chromium_missing_executable_is_unavailable(tmp_path: Path) -> None:
    provider = SystemChromiumWebSearchProvider(executable=tmp_path / "missing.exe")
    result = await provider.search("steel", 3)
    assert result["status"] == "unavailable"
    assert result["items"] == []
    assert result["live_data"] is False
