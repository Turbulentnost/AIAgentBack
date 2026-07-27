from __future__ import annotations

import asyncio
import base64
import html
import ipaddress
import json
import os
import re
import socket
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
import websockets

YANDEX_SEARCH_URL = "https://yandex.ru/search/?text={query}"
YANDEX_TOUCH_SEARCH_URL = "https://yandex.ru/search/touch/?text={query}"
DUCKDUCKGO_HTML_SEARCH_URL = "https://html.duckduckgo.com/html/?q={query}"
DUCKDUCKGO_LITE_SEARCH_URL = "https://lite.duckduckgo.com/lite/?q={query}"
BING_SEARCH_URL = (
    "https://www.bing.com/search?q={query}&setlang=ru-RU&cc=RU&mkt=ru-RU"
)
DDG_CHALLENGE_MARKERS = (
    "anomaly-modal",
    "anomaly.js",
    "challenge-form",
    "please complete the following challenge",
    "unusual traffic",
)
DEFAULT_YANDEX_PATHS = (
    Path(r"C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe"),
    Path(r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe"),
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Yandex"
    / "YandexBrowser"
    / "Application"
    / "browser.exe",
)
_LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", ""))
_PROGRAM_FILES = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
_PROGRAM_FILES_X86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
DEFAULT_EDGE_PATHS = (
    _PROGRAM_FILES_X86 / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    _PROGRAM_FILES / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    _LOCALAPPDATA / "Microsoft" / "Edge" / "Application" / "msedge.exe",
)
DEFAULT_CHROME_PATHS = (
    _PROGRAM_FILES / "Google" / "Chrome" / "Application" / "chrome.exe",
    _PROGRAM_FILES_X86 / "Google" / "Chrome" / "Application" / "chrome.exe",
    _LOCALAPPDATA / "Google" / "Chrome" / "Application" / "chrome.exe",
)
DEFAULT_CHROMIUM_PATHS = (
    _LOCALAPPDATA / "Chromium" / "Application" / "chrome.exe",
    _PROGRAM_FILES / "Chromium" / "Application" / "chrome.exe",
)
FORBIDDEN_PROFILE_MARKERS = (
    "YandexBrowser\\User Data",
    "YandexBrowser/User Data",
    "Google\\Chrome\\User Data",
    "Google/Chrome/User Data",
    "Microsoft\\Edge\\User Data",
    "Microsoft/Edge/User Data",
    "Chromium\\User Data",
    "Chromium/User Data",
)
SEARCH_ENGINE_HOSTS = {
    "duckduckgo.com",
    "html.duckduckgo.com",
    "www.duckduckgo.com",
    "duck.com",
    "bing.com",
    "www.bing.com",
    "yandex.ru",
    "ya.ru",
    "www.yandex.ru",
}
FORBIDDEN_DOWNLOAD_SUFFIXES = {
    ".bat",
    ".cmd",
    ".com",
    ".exe",
    ".msi",
    ".ps1",
    ".scr",
}
CAPTCHA_MARKERS = (
    "showcaptcha",
    "captcha__",
    "smartcaptcha",
    "checkbox-captcha",
    "робот",
    "вы не робот",
)
RETRYABLE_BROWSER_STATUSES = frozenset({"captcha", "timeout", "unavailable"})


def resolve_yandex_browser_path() -> Path | None:
    configured = os.environ.get("YANDEX_BROWSER_PATH")
    candidates = (Path(configured), *DEFAULT_YANDEX_PATHS) if configured else DEFAULT_YANDEX_PATHS
    return next((path for path in candidates if path.is_file()), None)


def resolve_system_browser_path(*, prefer: str | None = None) -> Path | None:
    """Resolve Edge/Chrome/Chromium for isolated headless search (never main profile)."""
    configured = os.environ.get("PROCUREMENT_WEB_BROWSER_PATH")
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    preference = (
        prefer
        or os.environ.get("PROCUREMENT_WEB_BROWSER_PREFER")
        or "edge"
    ).strip().casefold()
    ordered: list[Path] = []
    buckets = {
        "edge": DEFAULT_EDGE_PATHS,
        "chrome": DEFAULT_CHROME_PATHS,
        "chromium": DEFAULT_CHROMIUM_PATHS,
    }
    if preference in buckets:
        ordered.extend(buckets[preference])
        for name, paths in buckets.items():
            if name != preference:
                ordered.extend(paths)
    else:
        ordered.extend(
            [*DEFAULT_EDGE_PATHS, *DEFAULT_CHROME_PATHS, *DEFAULT_CHROMIUM_PATHS]
        )
    return next((path for path in ordered if path.is_file()), None)


def validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only public http/https URLs are allowed")
    host = parsed.hostname.casefold().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("Localhost URLs are forbidden")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Private, link-local, and reserved IP literals are forbidden")
    suffix = Path(unquote(parsed.path)).suffix.casefold()
    if suffix in FORBIDDEN_DOWNLOAD_SUFFIXES:
        raise ValueError("Executable downloads are forbidden")
    return url


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _decode_bing_u_param(value: str) -> str | None:
    """Decode Bing SERP redirect payload: u=a1<base64(url)>."""
    raw = html.unescape(value or "").strip()
    if not raw.startswith("a1") or len(raw) < 4:
        return None
    payload = raw[2:]
    padded = payload + "=" * (-len(payload) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            decoded = decoder(padded.encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if decoded.startswith(("http://", "https://")):
            return decoded
    return None


def _result_url(href: str) -> str | None:
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urlparse(href)
    host = (parsed.hostname or "").casefold()
    if host.endswith("yandex.ru") or host.endswith("ya.ru"):
        target = parse_qs(parsed.query).get("url", [None])[0]
        if target:
            href = unquote(target)
            parsed = urlparse(href)
            host = (parsed.hostname or "").casefold()
    if "uddg" in (parsed.query or "") or parsed.path.endswith("/l/"):
        target_list = parse_qs(parsed.query).get("uddg")
        if target_list:
            href = unquote(target_list[0])
            parsed = urlparse(href)
            host = (parsed.hostname or "").casefold()
    if host.endswith("bing.com") and "/ck/" in (parsed.path or ""):
        target = parse_qs(parsed.query).get("u", [None])[0]
        decoded = _decode_bing_u_param(target or "")
        if decoded:
            href = decoded
            parsed = urlparse(href)
            host = (parsed.hostname or "").casefold()
    bare = host.removeprefix("www.")
    if not host or host in SEARCH_ENGINE_HOSTS or bare in SEARCH_ENGINE_HOSTS:
        return None
    try:
        return validate_public_url(href)
    except ValueError:
        return None


def _cite_url(cite_html: str) -> str | None:
    text = _clean_text(cite_html)
    if not text:
        return None
    match = re.search(r"https?://[^\s›·]+", text)
    if match:
        return _result_url(match.group(0).rstrip(".,);"))
    # Bare domain in Bing cite, e.g. "supplier.example › catalog"
    domain = text.split()[0].split("›")[0].strip()
    if domain and "." in domain and "://" not in domain:
        return _result_url(f"https://{domain}")
    return None


def _is_ddg_challenge(document: str) -> bool:
    folded = document.casefold()
    return any(marker in folded for marker in DDG_CHALLENGE_MARKERS)


def parse_yandex_results(document: str, limit: int) -> list[dict[str, str]]:
    """Parse both current Organic results and compact Yandex result markup."""
    starts = list(
        re.finditer(
            r"<(?:li|div)[^>]+serp-item[^>]*>",
            document,
            flags=re.IGNORECASE,
        )
    )
    blocks = [
        document[match.start() : starts[index + 1].start() if index + 1 < len(starts) else None]
        for index, match in enumerate(starts)
    ]
    if not blocks:
        blocks = re.findall(
            r"<a\b[^>]*href=[\"'][^\"']+[\"'][^>]*>.*?</a>.*?"
            r"(?=<a\b[^>]*href=|$)",
            document,
            flags=re.IGNORECASE | re.DOTALL,
        )
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    anchor_pattern = re.compile(
        r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in blocks:
        anchors = anchor_pattern.findall(block)
        chosen: tuple[str, str] | None = None
        for href, label in anchors:
            title = _clean_text(label)
            target = _result_url(html.unescape(href))
            if target and title and len(title) > 2:
                chosen = (target, title)
                break
        if chosen is None or chosen[0] in seen:
            continue
        snippet_match = re.search(
            r"<(?:div|span)[^>]+(?:OrganicTextContentSpan|organic__text|TextContainer)[^>]*>"
            r"(.*?)</(?:div|span)>",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippet = _clean_text(snippet_match.group(1)) if snippet_match else ""
        seen.add(chosen[0])
        results.append({"title": chosen[1], "url": chosen[0], "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def parse_duckduckgo_results(document: str, limit: int) -> list[dict[str, str]]:
    """Parse DuckDuckGo HTML endpoint result cards into Yandex-compatible items."""
    starts = list(
        re.finditer(
            r"<div[^>]+class=[\"'][^\"']*\bresult\b[^\"']*[\"'][^>]*>",
            document,
            flags=re.IGNORECASE,
        )
    )
    blocks = [
        document[match.start() : starts[index + 1].start() if index + 1 < len(starts) else None]
        for index, match in enumerate(starts)
    ]
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    anchor_pattern = re.compile(
        r"<a\b[^>]*class=[\"'][^\"']*result__a[^\"']*[\"'][^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>"
        r"|<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*class=[\"'][^\"']*result__a[^\"']*[\"'][^>]*>(.*?)</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    snippet_pattern = re.compile(
        r"<(?:a|td)[^>]+class=[\"'][^\"']*result__snippet[^\"']*[\"'][^>]*>(.*?)</(?:a|td)>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in blocks or [document]:
        match = anchor_pattern.search(block)
        if not match:
            continue
        href = html.unescape(match.group(1) or match.group(3) or "")
        title = _clean_text(match.group(2) or match.group(4) or "")
        target = _result_url(href)
        if not target or not title or len(title) < 2 or target in seen:
            continue
        snippet_match = snippet_pattern.search(block)
        snippet = _clean_text(snippet_match.group(1)) if snippet_match else ""
        seen.add(target)
        results.append({"title": title, "url": target, "snippet": snippet})
        if len(results) >= limit:
            break
    if results:
        return results
    # Fallback: any non-engine anchors (same shape as browser_tools web_search).
    for href, label in re.findall(
        r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        document,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        title = _clean_text(label)
        target = _result_url(html.unescape(href))
        if not target or not title or len(title) < 2 or target in seen:
            continue
        seen.add(target)
        results.append({"title": title, "url": target, "snippet": ""})
        if len(results) >= limit:
            break
    return results


def parse_bing_results(document: str, limit: int) -> list[dict[str, str]]:
    """Parse Bing HTML organic results into Yandex-compatible items."""
    starts = list(
        re.finditer(
            r"<li[^>]+class=[\"'][^\"']*\bb_algo\b[^\"']*[\"'][^>]*>",
            document,
            flags=re.IGNORECASE,
        )
    )
    blocks = [
        document[match.start() : starts[index + 1].start() if index + 1 < len(starts) else None]
        for index, match in enumerate(starts)
    ]
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    anchor_pattern = re.compile(
        r"<h2[^>]*>\s*<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    snippet_pattern = re.compile(
        r"<p[^>]*class=[\"'][^\"']*b_lineclamp[^\"']*[\"'][^>]*>(.*?)</p>"
        r"|<p[^>]*>(.*?)</p>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    cite_pattern = re.compile(
        r"<cite[^>]*>(.*?)</cite>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in blocks:
        match = anchor_pattern.search(block)
        if not match:
            continue
        title = _clean_text(match.group(2))
        target = _result_url(html.unescape(match.group(1)))
        if target is None:
            cite_match = cite_pattern.search(block)
            if cite_match:
                target = _cite_url(cite_match.group(1))
        if not target or not title or len(title) < 2 or target in seen:
            continue
        snippet_match = snippet_pattern.search(block)
        snippet = ""
        if snippet_match:
            snippet = _clean_text(snippet_match.group(1) or snippet_match.group(2) or "")
        seen.add(target)
        results.append({"title": title, "url": target, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def parse_duckduckgo_lite_results(document: str, limit: int) -> list[dict[str, str]]:
    """Parse DuckDuckGo lite SERP links into Yandex-compatible items."""
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    skip_titles = {"duckduckgo", "next", "previous", "more results", "here"}
    for href, label in re.findall(
        r"<a\b[^>]*rel=[\"']nofollow[\"'][^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        document,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        title = _clean_text(label)
        target = _result_url(html.unescape(href))
        if not target or not title or len(title) < 2 or target in seen:
            continue
        if title.casefold() in skip_titles:
            continue
        seen.add(target)
        results.append({"title": title, "url": target, "snippet": ""})
        if len(results) >= limit:
            break
    if results:
        return results
    return parse_duckduckgo_results(document, limit)


class SupplierProvider(Protocol):
    async def search(self, query: str, category: str | None, limit: int) -> dict[str, Any]: ...

    async def profile(self, supplier_id: str) -> dict[str, Any]: ...

    async def related(self, supplier_id: str, capability: str) -> dict[str, Any]: ...


class BrowserSearchProvider(Protocol):
    async def search(self, query: str, limit: int) -> dict[str, Any]: ...

    async def fetch(self, url: str) -> dict[str, Any]: ...


class ApprovalProvider(Protocol):
    async def is_approved(self, approval_id: str, operation: str) -> bool: ...


class FixtureSupplierProvider:
    """Deterministic non-live provider; every response labels its provenance."""

    _SUPPLIERS: dict[str, dict[str, Any]] = {
        "fixture-steel": {
            "supplier_id": "fixture-steel",
            "name": "Проверенный поставщик металлопроката",
            "tax_id": "7700000001",
            "source": "internal",
            "categories": ["металл", "сталь", "прокат"],
            "quality_rating": "92",
            "delivery_rating": "86",
            "commercial_rating": "80",
            "is_active": True,
            "contacts": {"email": "fixture-steel@example.invalid"},
            "evidence": ["fixture:supplier:fixture-steel"],
        },
        "fixture-components": {
            "supplier_id": "fixture-components",
            "name": "Поставщик промышленных комплектующих",
            "tax_id": "7700000002",
            "source": "internal",
            "categories": ["комплектующие", "подшипники", "крепёж"],
            "quality_rating": "88",
            "delivery_rating": "90",
            "commercial_rating": "78",
            "is_active": True,
            "contacts": {"email": "fixture-components@example.invalid"},
            "evidence": ["fixture:supplier:fixture-components"],
        },
    }
    _RELATED: dict[str, list[dict[str, Any]]] = {
        "contracts": [
            {
                "contract_id": "fixture-contract-1",
                "number": "FIX-001",
                "status": "active",
            }
        ],
        "purchase_history": [
            {
                "order_id": "fixture-order-closed-1",
                "ordered_at": "2026-01-10",
                "amount": "150000.00",
                "currency": "RUB",
                "status": "received",
            }
        ],
        "quality_history": [
            {
                "inspection_id": "fixture-inspection-1",
                "result": "accepted",
                "nonconformities": 0,
            }
        ],
        "open_orders": [
            {
                "order_id": "fixture-order-open-1",
                "status": "confirmed",
                "expected_at": "2026-08-01",
            }
        ],
        "goods_in_transit": [
            {
                "shipment_id": "fixture-shipment-1",
                "status": "in_transit",
                "expected_at": "2026-07-30",
            }
        ],
    }

    @staticmethod
    def _envelope(
        capability: str,
        *,
        items: list[dict[str, Any]] | None = None,
        item: dict[str, Any] | None = None,
        status: str = "available",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": status,
            "capability": capability,
            "live_data": False,
            "provider": "deterministic_fixture",
            "provenance": [
                {
                    "source": "fixture",
                    "provider": "deterministic_fixture",
                    "live": False,
                }
            ],
        }
        if items is not None:
            payload["items"] = items
        if item is not None:
            payload["item"] = item
        return payload

    async def search(self, query: str, category: str | None, limit: int) -> dict[str, Any]:
        stopwords = {
            "поставщик",
            "поставщика",
            "поставщики",
            "supplier",
            "suppliers",
            "проверенный",
            "промышленных",
            "промышленный",
        }
        terms = {
            token
            for token in f"{query} {category or ''}".casefold().replace(",", " ").split()
            if token and token not in stopwords
        }
        ranked: list[tuple[int, dict[str, Any]]] = []
        if terms:
            for supplier in self._SUPPLIERS.values():
                categories = [str(value) for value in supplier.get("categories") or []]
                haystack = " ".join(
                    [str(supplier["name"]), *categories]
                ).casefold()
                score = sum(term in haystack for term in terms)
                if score:
                    ranked.append((score, supplier))
            ranked.sort(key=lambda value: (-value[0], str(value[1]["supplier_id"])))
        items = [deepcopy(value[1]) for value in ranked[:limit]]
        return self._envelope("supplier_search_internal", items=items)

    async def profile(self, supplier_id: str) -> dict[str, Any]:
        item = self._SUPPLIERS.get(supplier_id)
        if item is None:
            return self._envelope(
                "supplier_get_profile",
                status="unavailable",
                item={
                    "supplier_id": supplier_id,
                    "reason": "supplier_not_found_in_fixture",
                },
            )
        return self._envelope("supplier_get_profile", item=deepcopy(item))

    async def related(self, supplier_id: str, capability: str) -> dict[str, Any]:
        if supplier_id not in self._SUPPLIERS:
            return self._envelope(capability, status="unavailable", items=[])
        return self._envelope(
            capability,
            items=[
                {**deepcopy(item), "supplier_id": supplier_id}
                for item in self._RELATED.get(capability.removeprefix("supplier_get_"), [])
            ],
        )


class WaitingBrowserProvider:
    async def search(self, query: str, limit: int) -> dict[str, Any]:
        return {
            "status": "waiting_browser",
            "capability": "supplier_search_web",
            "live_data": False,
            "provider": "browser_runs_adapter",
            "query": query,
            "limit": limit,
            "items": [],
            "provenance": [],
            "message": "Controlled browser-runs provider is not configured.",
        }

    async def fetch(self, url: str) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "capability": "supplier_fetch_page",
            "live_data": False,
            "provider": "browser_runs_adapter",
            "url": url,
            "provenance": [],
            "message": "Controlled browser provider is not configured.",
        }


def _allocate_debug_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """Kill the browser and any child renderers so timeouts leave no zombies."""
    if process.returncode is not None:
        return
    if os.name == "nt" and process.pid:
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


class _CdpSession:
    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket
        self._next_id = 0

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        self._next_id += 1
        message_id = self._next_id
        payload: dict[str, Any] = {
            "id": message_id,
            "method": method,
            "params": params or {},
        }
        if session_id is not None:
            payload["sessionId"] = session_id
        await self._websocket.send(json.dumps(payload))
        while True:
            raw = await self._websocket.recv()
            data = json.loads(raw)
            if data.get("id") != message_id:
                continue
            if "error" in data:
                raise RuntimeError(str(data["error"]))
            result = data.get("result")
            return result if isinstance(result, dict) else {}


class IsolatedSystemBrowserProvider:
    """Isolated headless Chromium-family browser via CDP; never opens a user profile."""

    provider_name = "system_browser"
    profile_prefix = "procurement-browser-"
    missing_executable_message = "System browser executable is unavailable"
    timeout_message = "System browser timed out"
    empty_document_message = "System browser returned an empty document"

    def __init__(
        self,
        *,
        executable: Path | str | None = None,
        timeout_seconds: float | None = None,
        max_page_bytes: int | None = None,
        max_results: int | None = None,
    ) -> None:
        self.executable = Path(executable) if executable else None
        self.timeout_seconds = timeout_seconds or float(
            os.environ.get(
                "PROCUREMENT_WEB_BROWSER_TIMEOUT_SECONDS",
                os.environ.get("YANDEX_BROWSER_TIMEOUT_SECONDS", "60"),
            )
        )
        self.max_page_bytes = max_page_bytes or int(
            os.environ.get(
                "PROCUREMENT_WEB_BROWSER_MAX_PAGE_BYTES",
                os.environ.get("YANDEX_BROWSER_MAX_PAGE_BYTES", "2000000"),
            )
        )
        self.max_results = max_results or int(
            os.environ.get(
                "PROCUREMENT_WEB_BROWSER_MAX_RESULTS",
                os.environ.get("YANDEX_BROWSER_MAX_RESULTS", "20"),
            )
        )
        self._version: str | None = None

    def _browser_command(self, *, profile: str, port: int) -> list[str]:
        """Background headless Chromium flags; never the default user profile."""
        return [
            str(self.executable),
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-sync",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-download-notification",
            "--disable-popup-blocking",
            "--disable-blink-features=AutomationControlled",
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile}",
            "about:blank",
        ]

    async def fetch(self, url: str) -> dict[str, Any]:
        try:
            safe_url = validate_public_url(url)
        except ValueError as exc:
            return self._unavailable("supplier_fetch_page", str(exc), url=url)
        response = await self._browse(safe_url, "supplier_fetch_page")
        if response["status"] != "available":
            return response
        document = str(response.pop("_document"))
        if self._is_captcha(document):
            return {
                **response,
                "status": "captcha",
                "live_data": False,
                "message": "Page requested CAPTCHA verification.",
            }
        # Keep truncated HTML for product parsers (price/city selectors); content is cleaned text.
        html_cap = min(self.max_page_bytes, 400_000)
        return {
            **response,
            "live_data": True,
            "content": _clean_text(document),
            "html": document[:html_cap],
            "content_bytes": len(document.encode("utf-8")),
            "provenance": [
                {"source": safe_url, "provider": self.provider_name, "live": True}
            ],
        }

    async def _browse(self, url: str, capability: str) -> dict[str, Any]:
        if self.executable is None or not self.executable.is_file():
            return self._unavailable(
                capability,
                self.missing_executable_message,
                url=url,
            )
        port = _allocate_debug_port()
        process: asyncio.subprocess.Process | None = None
        document = ""
        failure: dict[str, Any] | None = None
        # ignore_cleanup_errors: Chromium can keep cache files locked briefly after kill.
        with tempfile.TemporaryDirectory(
            prefix=self.profile_prefix,
            ignore_cleanup_errors=True,
        ) as profile:
            if any(marker in profile for marker in FORBIDDEN_PROFILE_MARKERS):
                return self._unavailable(
                    capability,
                    "Refusing to use installed browser user profile directory",
                    url=url,
                )
            command = self._browser_command(profile=profile, port=port)
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.DEVNULL,
                    # DEVNULL: PIPE without a reader can fill and stall Chromium.
                    stderr=asyncio.subprocess.DEVNULL,
                )
                document = await asyncio.wait_for(
                    self._fetch_dom_via_cdp(port=port, url=url),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError:
                failure = self._unavailable(
                    capability,
                    self.timeout_message,
                    "timeout",
                    url,
                )
            except OSError as exc:
                failure = self._unavailable(
                    capability,
                    f"{self.provider_name} failed: {exc}",
                    url=url,
                )
            except Exception as exc:
                failure = self._unavailable(
                    capability,
                    f"{self.provider_name} CDP failed: {exc}",
                    url=url,
                )
            finally:
                if process is not None:
                    await _terminate_process_tree(process)
                    # Give Windows a moment to release profile locks before rmtree.
                    await asyncio.sleep(0.2)
        if failure is not None:
            return failure
        if len(document.encode("utf-8")) > self.max_page_bytes:
            return self._unavailable(
                capability,
                "Browser page exceeded maximum size",
                url=url,
            )
        if not document.strip():
            return self._unavailable(
                capability,
                self.empty_document_message,
                url=url,
            )
        return {
            "status": "available",
            "capability": capability,
            "provider": self.provider_name,
            "browser_path": str(self.executable),
            "browser_version": self._version,
            "url": url,
            "live_data": False,
            "provenance": [],
            "_document": document,
        }

    async def _wait_for_debugger_url(self, port: int) -> str:
        deadline = asyncio.get_running_loop().time() + min(15.0, self.timeout_seconds)
        async with httpx.AsyncClient() as client:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    response = await client.get(
                        f"http://127.0.0.1:{port}/json/version",
                        timeout=1.0,
                    )
                    if response.status_code == 200:
                        payload = response.json()
                        version = payload.get("Browser")
                        if isinstance(version, str):
                            self._version = version
                        ws_url = payload.get("webSocketDebuggerUrl")
                        if isinstance(ws_url, str) and ws_url:
                            return ws_url
                except (httpx.HTTPError, ValueError, KeyError):
                    pass
                await asyncio.sleep(0.15)
        raise TimeoutError("Chrome DevTools endpoint did not become ready")

    def _headless_user_agent(self) -> str:
        """Non-HeadlessChrome UA: Bing/DDG otherwise cloak headless SERPs."""
        configured = os.environ.get("PROCUREMENT_WEB_BROWSER_USER_AGENT")
        if configured:
            return configured.strip()
        name = (self.executable.name if self.executable else "").casefold()
        if "msedge" in name or "edge" in name:
            return (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
            )
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

    async def _fetch_dom_via_cdp(self, *, port: int, url: str) -> str:
        ws_url = await self._wait_for_debugger_url(port)
        async with websockets.connect(ws_url, max_size=self.max_page_bytes + 1024) as websocket:
            cdp = _CdpSession(websocket)
            created = await cdp.call("Target.createTarget", {"url": "about:blank"})
            target_id = str(created["targetId"])
            attached = await cdp.call(
                "Target.attachToTarget",
                {"targetId": target_id, "flatten": True},
            )
            session_id = str(attached["sessionId"])
            await cdp.call("Page.enable", session_id=session_id)
            await cdp.call("Runtime.enable", session_id=session_id)
            await cdp.call("Network.enable", session_id=session_id)
            await cdp.call(
                "Network.setUserAgentOverride",
                {
                    "userAgent": self._headless_user_agent(),
                    "acceptLanguage": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                    "platform": "Win32",
                },
                session_id=session_id,
            )
            try:
                await cdp.call(
                    "Emulation.setLocaleOverride",
                    {"locale": "ru-RU"},
                    session_id=session_id,
                )
            except RuntimeError:
                pass
            await cdp.call(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": (
                        "Object.defineProperty(navigator, 'webdriver', "
                        "{get: () => undefined});"
                    )
                },
                session_id=session_id,
            )
            await cdp.call("Page.navigate", {"url": url}, session_id=session_id)
            document = ""
            settle_deadline = asyncio.get_running_loop().time() + min(
                25.0, max(5.0, self.timeout_seconds - 2.0)
            )
            while asyncio.get_running_loop().time() < settle_deadline:
                evaluated = await cdp.call(
                    "Runtime.evaluate",
                    {
                        "expression": (
                            "(() => {"
                            " const ready = document.readyState;"
                            " const html = document.documentElement.outerHTML;"
                            " const hasAlgo = !!document.querySelector('li.b_algo h2 a');"
                            " const hasDdg = !!document.querySelector('a.result__a, "
                            "  a[rel=\"nofollow\"]');"
                            " return [ready, hasAlgo ? '1' : '0',"
                            "  hasDdg ? '1' : '0', html].join('\\n');"
                            "})()"
                        ),
                        "returnByValue": True,
                    },
                    session_id=session_id,
                )
                value = evaluated.get("result", {}).get("value")
                if isinstance(value, str):
                    parts = value.split("\n", 3)
                    if len(parts) == 4:
                        ready_state, has_algo, has_ddg, html_document = parts
                        document = html_document
                        if self._is_captcha(document) or _is_ddg_challenge(document):
                            break
                        if ready_state == "complete" and len(document) > 512:
                            if has_algo == "1" or has_ddg == "1":
                                break
                            if asyncio.get_running_loop().time() + 2.0 >= settle_deadline:
                                break
                    elif "\n" in value:
                        ready_state, html_document = value.split("\n", 1)
                        document = html_document
                        if ready_state == "complete" and len(document) > 512:
                            break
                        if self._is_captcha(document):
                            break
                await asyncio.sleep(0.35)
            if not document:
                evaluated = await cdp.call(
                    "Runtime.evaluate",
                    {
                        "expression": "document.documentElement.outerHTML",
                        "returnByValue": True,
                    },
                    session_id=session_id,
                )
                document = str(evaluated.get("result", {}).get("value") or "")
            try:
                await cdp.call("Target.closeTarget", {"targetId": target_id})
            except RuntimeError:
                pass
            return document

    @staticmethod
    def _is_captcha(document: str) -> bool:
        folded = document.casefold()
        return any(marker in folded for marker in CAPTCHA_MARKERS)

    def _unavailable(
        self,
        capability: str,
        message: str,
        status: str = "unavailable",
        url: str | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "capability": capability,
            "provider": self.provider_name,
            "browser_path": str(self.executable) if self.executable else None,
            "browser_version": self._version,
            "url": url,
            "live_data": False,
            "provenance": [],
            "message": message,
        }


class SystemYandexBrowserProvider(IsolatedSystemBrowserProvider):
    """Isolated headless Yandex Browser via CDP; never opens the user profile/window."""

    provider_name = "system_yandex"
    profile_prefix = "procurement-yandex-"
    missing_executable_message = "Yandex Browser executable is unavailable"
    timeout_message = "Yandex Browser timed out"
    empty_document_message = "Yandex Browser returned an empty document"

    def __init__(
        self,
        *,
        executable: Path | str | None = None,
        timeout_seconds: float | None = None,
        max_page_bytes: int | None = None,
        max_results: int | None = None,
    ) -> None:
        resolved = Path(executable) if executable else resolve_yandex_browser_path()
        super().__init__(
            executable=resolved,
            timeout_seconds=timeout_seconds,
            max_page_bytes=max_page_bytes,
            max_results=max_results,
        )

    async def search(self, query: str, limit: int) -> dict[str, Any]:
        effective_limit = max(1, min(limit, self.max_results))
        encoded = quote_plus(query)
        search_urls = (
            YANDEX_SEARCH_URL.format(query=encoded),
            YANDEX_TOUCH_SEARCH_URL.format(query=encoded),
        )
        last_failure: dict[str, Any] | None = None
        for url in search_urls:
            response = await self._browse(url, "supplier_search_web")
            if response["status"] != "available":
                last_failure = {**response, "query": query, "items": []}
                continue
            document = str(response.pop("_document"))
            if self._is_captcha(document):
                last_failure = {
                    **response,
                    "status": "captcha",
                    "live_data": False,
                    "query": query,
                    "items": [],
                    "message": (
                        "Yandex SmartCaptcha blocked headless search "
                        "(isolated background browser; main profile unused)."
                    ),
                }
                continue
            items = parse_yandex_results(document, effective_limit)
            if not items:
                last_failure = {
                    **response,
                    "status": "unavailable",
                    "live_data": False,
                    "query": query,
                    "items": [],
                    "message": "Yandex returned no parseable browser results.",
                }
                continue
            return {
                **response,
                "query": query,
                "items": items,
                "live_data": True,
                "provenance": [
                    {"source": item["url"], "provider": self.provider_name, "live": True}
                    for item in items
                ],
            }
        return last_failure or {
            "status": "unavailable",
            "capability": "supplier_search_web",
            "provider": self.provider_name,
            "query": query,
            "items": [],
            "live_data": False,
            "provenance": [],
            "message": "Yandex Browser search failed",
        }


class SystemChromiumWebSearchProvider(IsolatedSystemBrowserProvider):
    """Isolated Edge/Chrome/Chromium + DuckDuckGo/Bing; preferred headless web search."""

    provider_name = "system_chromium"
    profile_prefix = "procurement-chromium-"
    missing_executable_message = "Edge/Chrome/Chromium executable is unavailable"
    timeout_message = "System Chromium browser timed out"
    empty_document_message = "System Chromium browser returned an empty document"

    def __init__(
        self,
        *,
        executable: Path | str | None = None,
        search_engine: str | None = None,
        timeout_seconds: float | None = None,
        max_page_bytes: int | None = None,
        max_results: int | None = None,
    ) -> None:
        resolved = Path(executable) if executable else resolve_system_browser_path()
        super().__init__(
            executable=resolved,
            timeout_seconds=timeout_seconds,
            max_page_bytes=max_page_bytes,
            max_results=max_results,
        )
        engine = (
            search_engine
            or os.environ.get("PROCUREMENT_WEB_SEARCH_ENGINE")
            or "bing"
        ).strip().casefold()
        if engine not in {"duckduckgo", "bing"}:
            engine = "bing"
        self.search_engine = engine

    def _search_urls(self, query: str) -> tuple[str, ...]:
        encoded = quote_plus(query)
        bing = BING_SEARCH_URL.format(query=encoded)
        if self.search_engine == "duckduckgo":
            # DDG is often challenge-walled in headless; keep Bing as backup.
            return (
                DUCKDUCKGO_LITE_SEARCH_URL.format(query=encoded),
                DUCKDUCKGO_HTML_SEARCH_URL.format(query=encoded),
                bing,
            )
        return (bing,)

    def _parse_results(self, document: str, limit: int, *, url: str) -> list[dict[str, str]]:
        host = (urlparse(url).hostname or "").casefold()
        if "lite.duckduckgo.com" in host:
            return parse_duckduckgo_lite_results(document, limit)
        if "duckduckgo.com" in host:
            return parse_duckduckgo_results(document, limit)
        if "bing.com" in host:
            return parse_bing_results(document, limit)
        return (
            parse_duckduckgo_lite_results(document, limit)
            or parse_duckduckgo_results(document, limit)
            or parse_bing_results(document, limit)
        )

    def _engine_label(self, url: str) -> str:
        host = (urlparse(url).hostname or "").casefold()
        if "duckduckgo.com" in host:
            return "duckduckgo"
        if "bing.com" in host:
            return "bing"
        return self.search_engine

    async def search(self, query: str, limit: int) -> dict[str, Any]:
        effective_limit = max(1, min(limit, self.max_results))
        last_failure: dict[str, Any] | None = None
        for url in self._search_urls(query):
            response = await self._browse(url, "supplier_search_web")
            if response["status"] != "available":
                last_failure = {
                    **response,
                    "query": query,
                    "items": [],
                    "search_engine": self.search_engine,
                }
                continue
            document = str(response.pop("_document"))
            if self._is_captcha(document) or _is_ddg_challenge(document):
                last_failure = {
                    **response,
                    "status": "captcha",
                    "live_data": False,
                    "query": query,
                    "items": [],
                    "search_engine": self._engine_label(url),
                    "message": (
                        "Search engine CAPTCHA/challenge blocked headless Chromium search "
                        "(isolated temp profile; main browser profile unused)."
                    ),
                }
                continue
            items = self._parse_results(document, effective_limit, url=url)
            if not items:
                last_failure = {
                    **response,
                    "status": "unavailable",
                    "live_data": False,
                    "query": query,
                    "items": [],
                    "search_engine": self._engine_label(url),
                    "message": "Chromium web search returned no parseable results.",
                }
                continue
            engine_used = self._engine_label(url)
            return {
                **response,
                "query": query,
                "items": items,
                "live_data": True,
                "search_engine": engine_used,
                "provenance": [
                    {
                        "source": item["url"],
                        "provider": self.provider_name,
                        "search_engine": engine_used,
                        "live": True,
                    }
                    for item in items
                ],
            }
        return last_failure or {
            "status": "unavailable",
            "capability": "supplier_search_web",
            "provider": self.provider_name,
            "query": query,
            "items": [],
            "live_data": False,
            "provenance": [],
            "search_engine": self.search_engine,
            "message": "Chromium web search failed",
        }


class HttpSerpSearchProvider:
    """Fast HTTP SERP (DuckDuckGo HTML/Lite, optional Bing) — no browser process.

    Used as the default primary in ``auto`` mode because headless Edge/Bing often
    hits CAPTCHA or 45–60s CDP timeouts on Windows, while DDG HTML returns in ~2s.
    """

    provider_name = "http_serp"

    def __init__(
        self,
        *,
        timeout_seconds: float | None = None,
        max_page_bytes: int | None = None,
        max_results: int | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.environ.get(
                "PROCUREMENT_WEB_HTTP_TIMEOUT_SECONDS",
                os.environ.get("PROCUREMENT_WEB_BROWSER_TIMEOUT_SECONDS", "20"),
            )
        )
        self.max_page_bytes = max_page_bytes or int(
            os.environ.get(
                "PROCUREMENT_WEB_BROWSER_MAX_PAGE_BYTES",
                os.environ.get("YANDEX_BROWSER_MAX_PAGE_BYTES", "2000000"),
            )
        )
        self.max_results = max_results or int(
            os.environ.get(
                "PROCUREMENT_WEB_BROWSER_MAX_RESULTS",
                os.environ.get("YANDEX_BROWSER_MAX_RESULTS", "20"),
            )
        )
        self.user_agent = (
            user_agent
            or os.environ.get("PROCUREMENT_WEB_BROWSER_USER_AGENT")
            or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        ).strip()

    def _search_urls(self, query: str) -> tuple[str, ...]:
        encoded = quote_plus(query)
        engine = (
            os.environ.get("PROCUREMENT_WEB_HTTP_SEARCH_ENGINE")
            or os.environ.get("PROCUREMENT_WEB_SEARCH_ENGINE")
            or "duckduckgo"
        ).strip().casefold()
        ddg_lite = DUCKDUCKGO_LITE_SEARCH_URL.format(query=encoded)
        ddg_html = DUCKDUCKGO_HTML_SEARCH_URL.format(query=encoded)
        bing = BING_SEARCH_URL.format(query=encoded)
        if engine == "bing":
            return (bing, ddg_html, ddg_lite)
        # Default: DDG first — Bing HTTP is often blocked/slow without a real browser.
        return (ddg_html, ddg_lite, bing)

    def _parse_results(self, document: str, limit: int, *, url: str) -> list[dict[str, str]]:
        host = (urlparse(url).hostname or "").casefold()
        if "lite.duckduckgo.com" in host:
            return parse_duckduckgo_lite_results(document, limit)
        if "duckduckgo.com" in host:
            return parse_duckduckgo_results(document, limit)
        if "bing.com" in host:
            return parse_bing_results(document, limit)
        return (
            parse_duckduckgo_results(document, limit)
            or parse_duckduckgo_lite_results(document, limit)
            or parse_bing_results(document, limit)
        )

    def _engine_label(self, url: str) -> str:
        host = (urlparse(url).hostname or "").casefold()
        if "duckduckgo.com" in host:
            return "duckduckgo"
        if "bing.com" in host:
            return "bing"
        return "http"

    async def _get_document(self, url: str) -> tuple[str | None, str | None]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                headers=headers,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.get(url)
        except httpx.TimeoutException:
            return None, "timeout"
        except httpx.HTTPError as exc:
            return None, f"http_error:{type(exc).__name__}"
        if response.status_code >= 400:
            return None, f"http_{response.status_code}"
        document = response.text
        if len(document.encode("utf-8")) > self.max_page_bytes:
            return None, "page_too_large"
        if not document.strip():
            return None, "empty_document"
        return document, None

    async def search(self, query: str, limit: int) -> dict[str, Any]:
        effective_limit = max(1, min(limit, self.max_results))
        last_failure: dict[str, Any] | None = None
        for url in self._search_urls(query):
            document, error = await self._get_document(url)
            engine = self._engine_label(url)
            if document is None:
                status = "timeout" if error == "timeout" else "unavailable"
                last_failure = {
                    "status": status,
                    "capability": "supplier_search_web",
                    "provider": self.provider_name,
                    "query": query,
                    "items": [],
                    "live_data": False,
                    "provenance": [],
                    "search_engine": engine,
                    "url": url,
                    "message": f"HTTP SERP failed ({error})",
                }
                continue
            if _is_ddg_challenge(document) or any(
                marker in document.casefold() for marker in CAPTCHA_MARKERS
            ):
                last_failure = {
                    "status": "captcha",
                    "capability": "supplier_search_web",
                    "provider": self.provider_name,
                    "query": query,
                    "items": [],
                    "live_data": False,
                    "provenance": [],
                    "search_engine": engine,
                    "url": url,
                    "message": "HTTP SERP blocked by CAPTCHA/challenge",
                }
                continue
            items = self._parse_results(document, effective_limit, url=url)
            if not items:
                last_failure = {
                    "status": "unavailable",
                    "capability": "supplier_search_web",
                    "provider": self.provider_name,
                    "query": query,
                    "items": [],
                    "live_data": False,
                    "provenance": [],
                    "search_engine": engine,
                    "url": url,
                    "message": "HTTP SERP returned no parseable results",
                }
                continue
            return {
                "status": "available",
                "capability": "supplier_search_web",
                "provider": self.provider_name,
                "query": query,
                "items": items,
                "live_data": True,
                "search_engine": engine,
                "url": url,
                "provenance": [
                    {
                        "source": item["url"],
                        "provider": self.provider_name,
                        "search_engine": engine,
                        "live": True,
                    }
                    for item in items
                ],
            }
        return last_failure or {
            "status": "unavailable",
            "capability": "supplier_search_web",
            "provider": self.provider_name,
            "query": query,
            "items": [],
            "live_data": False,
            "provenance": [],
            "message": "HTTP SERP search failed",
        }

    async def fetch(self, url: str) -> dict[str, Any]:
        try:
            safe_url = validate_public_url(url)
        except ValueError as exc:
            return {
                "status": "unavailable",
                "capability": "supplier_fetch_page",
                "provider": self.provider_name,
                "url": url,
                "live_data": False,
                "message": str(exc),
            }
        document, error = await self._get_document(safe_url)
        if document is None:
            status = "timeout" if error == "timeout" else "unavailable"
            return {
                "status": status,
                "capability": "supplier_fetch_page",
                "provider": self.provider_name,
                "url": safe_url,
                "live_data": False,
                "message": f"HTTP fetch failed ({error})",
            }
        if any(marker in document.casefold() for marker in CAPTCHA_MARKERS):
            return {
                "status": "captcha",
                "capability": "supplier_fetch_page",
                "provider": self.provider_name,
                "url": safe_url,
                "live_data": False,
                "message": "Page requested CAPTCHA verification.",
            }
        html_cap = min(self.max_page_bytes, 400_000)
        return {
            "status": "available",
            "capability": "supplier_fetch_page",
            "provider": self.provider_name,
            "url": safe_url,
            "live_data": True,
            "content": _clean_text(document),
            "html": document[:html_cap],
            "content_bytes": len(document.encode("utf-8")),
            "provenance": [
                {"source": safe_url, "provider": self.provider_name, "live": True}
            ],
        }


class FallbackBrowserSearchProvider:
    """Try primary browser search; on captcha/timeout/unavailable use fallback."""

    def __init__(
        self,
        *,
        primary: BrowserSearchProvider,
        fallback: BrowserSearchProvider | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback

    @staticmethod
    def _succeeded(result: dict[str, Any]) -> bool:
        return result.get("status") == "available" and bool(result.get("items"))

    @staticmethod
    def _should_fallback(result: dict[str, Any]) -> bool:
        if result.get("status") in RETRYABLE_BROWSER_STATUSES:
            return True
        return result.get("status") == "available" and not result.get("items")

    async def search(self, query: str, limit: int) -> dict[str, Any]:
        primary_result = await self.primary.search(query, limit)
        if self._succeeded(primary_result) or self.fallback is None:
            return {**primary_result, "fallback_used": False}
        if not self._should_fallback(primary_result):
            return {**primary_result, "fallback_used": False}
        fallback_result = await self.fallback.search(query, limit)
        if self._succeeded(fallback_result):
            return {
                **fallback_result,
                "fallback_used": True,
                "primary_status": primary_result.get("status"),
                "primary_provider": primary_result.get("provider"),
                "primary_message": primary_result.get("message"),
            }
        return {
            **fallback_result,
            "fallback_used": True,
            "primary_status": primary_result.get("status"),
            "primary_provider": primary_result.get("provider"),
            "primary_message": primary_result.get("message"),
            "message": (
                f"Primary ({primary_result.get('provider')}): "
                f"{primary_result.get('message')}; "
                f"Fallback ({fallback_result.get('provider')}): "
                f"{fallback_result.get('message')}"
            ),
        }

    async def fetch(self, url: str) -> dict[str, Any]:
        primary_result = await self.primary.fetch(url)
        if primary_result.get("status") == "available" or self.fallback is None:
            return {**primary_result, "fallback_used": False}
        if primary_result.get("status") not in RETRYABLE_BROWSER_STATUSES:
            return {**primary_result, "fallback_used": False}
        fallback_result = await self.fallback.fetch(url)
        return {
            **fallback_result,
            "fallback_used": True,
            "primary_status": primary_result.get("status"),
            "primary_provider": primary_result.get("provider"),
            "primary_message": primary_result.get("message"),
        }


def build_default_browser_search_provider() -> BrowserSearchProvider:
    """Select HTTP / Chromium / Yandex search strategy from environment.

    PROCUREMENT_WEB_SEARCH_PROVIDER:
      - auto (default): HTTP DuckDuckGo SERP first; Edge/Chrome then Yandex fallback
      - http|httpx|ddg_http: HTTP SERP only (no browser)
      - chromium|chrome|edge|duckduckgo: Chromium path only
      - yandex: Yandex only
      - yandex_first: Yandex then Chromium on captcha/timeout/unavailable
      - browser_first: Edge/Chrome first, then HTTP, then Yandex
    """
    mode = (
        os.environ.get("PROCUREMENT_WEB_SEARCH_PROVIDER") or "auto"
    ).strip().casefold()
    http = HttpSerpSearchProvider()
    chromium = SystemChromiumWebSearchProvider()
    yandex = SystemYandexBrowserProvider()
    browser_chain = FallbackBrowserSearchProvider(primary=chromium, fallback=yandex)
    if mode in {"yandex"}:
        return yandex
    if mode in {"http", "httpx", "ddg_http", "duckduckgo_http"}:
        return http
    if mode in {"chromium", "chrome", "edge", "duckduckgo"}:
        return chromium
    if mode in {"yandex_first"}:
        return FallbackBrowserSearchProvider(primary=yandex, fallback=chromium)
    if mode in {"browser_first", "chromium_first"}:
        # Legacy preference: headless browser, then fast HTTP if CDP fails.
        return FallbackBrowserSearchProvider(primary=browser_chain, fallback=http)
    # auto: HTTP SERP is reliable; browser remains fallback for blocked HTTP / fetch.
    return FallbackBrowserSearchProvider(primary=http, fallback=browser_chain)


class EnvironmentApprovalProvider:
    """Test/development approval seam; defaults to no approved mutations."""

    def __init__(self, approved: set[tuple[str, str]] | None = None) -> None:
        if approved is not None:
            self.approved = approved
            return
        configured = os.environ.get("PROCUREMENT_SUPPLIER_MCP_APPROVALS", "")
        self.approved = {
            (approval_id.strip(), operation.strip())
            for pair in configured.split(",")
            if ":" in pair
            for approval_id, operation in [pair.split(":", 1)]
        }

    async def is_approved(self, approval_id: str, operation: str) -> bool:
        return (approval_id, operation) in self.approved


__all__ = [
    "ApprovalProvider",
    "BrowserSearchProvider",
    "EnvironmentApprovalProvider",
    "FallbackBrowserSearchProvider",
    "FixtureSupplierProvider",
    "HttpSerpSearchProvider",
    "IsolatedSystemBrowserProvider",
    "SystemChromiumWebSearchProvider",
    "SystemYandexBrowserProvider",
    "SupplierProvider",
    "WaitingBrowserProvider",
    "build_default_browser_search_provider",
    "parse_bing_results",
    "parse_duckduckgo_lite_results",
    "parse_duckduckgo_results",
    "parse_yandex_results",
    "resolve_system_browser_path",
    "resolve_yandex_browser_path",
    "validate_public_url",
]
