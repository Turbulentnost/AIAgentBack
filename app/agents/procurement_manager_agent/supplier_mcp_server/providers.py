from __future__ import annotations

import asyncio
import html
import ipaddress
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

YANDEX_SEARCH_URL = "https://yandex.ru/search/?text={query}"
DEFAULT_YANDEX_PATHS = (
    Path(r"C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe"),
    Path(r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe"),
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Yandex"
    / "YandexBrowser"
    / "Application"
    / "browser.exe",
)
FORBIDDEN_DOWNLOAD_SUFFIXES = {
    ".bat",
    ".cmd",
    ".com",
    ".exe",
    ".msi",
    ".ps1",
    ".scr",
}


def resolve_yandex_browser_path() -> Path | None:
    configured = os.environ.get("YANDEX_BROWSER_PATH")
    candidates = (Path(configured), *DEFAULT_YANDEX_PATHS) if configured else DEFAULT_YANDEX_PATHS
    return next((path for path in candidates if path.is_file()), None)


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


def _result_url(href: str) -> str | None:
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urlparse(href)
    if parsed.hostname and parsed.hostname.endswith("yandex.ru"):
        target = parse_qs(parsed.query).get("url", [None])[0]
        if target:
            href = unquote(target)
    try:
        return validate_public_url(href)
    except ValueError:
        return None


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
        terms = {
            token
            for token in f"{query} {category or ''}".casefold().replace(",", " ").split()
            if token
        }
        ranked: list[tuple[int, dict[str, Any]]] = []
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


class SystemYandexBrowserProvider:
    """Runs an isolated system Yandex Chromium child and never uses the user profile."""

    def __init__(
        self,
        *,
        executable: Path | str | None = None,
        timeout_seconds: float | None = None,
        max_page_bytes: int | None = None,
        max_results: int | None = None,
    ) -> None:
        self.executable = Path(executable) if executable else resolve_yandex_browser_path()
        self.timeout_seconds = timeout_seconds or float(
            os.environ.get("YANDEX_BROWSER_TIMEOUT_SECONDS", "20")
        )
        self.max_page_bytes = max_page_bytes or int(
            os.environ.get("YANDEX_BROWSER_MAX_PAGE_BYTES", "2000000")
        )
        self.max_results = max_results or int(
            os.environ.get("YANDEX_BROWSER_MAX_RESULTS", "20")
        )
        self._version: str | None = None

    async def search(self, query: str, limit: int) -> dict[str, Any]:
        effective_limit = max(1, min(limit, self.max_results))
        url = YANDEX_SEARCH_URL.format(query=quote_plus(query))
        response = await self._browse(url, "supplier_search_web")
        if response["status"] != "available":
            return {**response, "query": query, "items": []}
        document = str(response.pop("_document"))
        if self._is_captcha(document):
            return {
                **response,
                "status": "captcha",
                "live_data": False,
                "query": query,
                "items": [],
                "message": "Yandex requested CAPTCHA verification.",
            }
        items = parse_yandex_results(document, effective_limit)
        if not items:
            return {
                **response,
                "status": "unavailable",
                "live_data": False,
                "query": query,
                "items": [],
                "message": "Yandex returned no parseable browser results.",
            }
        return {
            **response,
            "query": query,
            "items": items,
            "live_data": True,
            "provenance": [
                {"source": item["url"], "provider": "system_yandex", "live": True}
                for item in items
            ],
        }

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
        return {
            **response,
            "live_data": True,
            "content": _clean_text(document),
            "content_bytes": len(document.encode("utf-8")),
            "provenance": [
                {"source": safe_url, "provider": "system_yandex", "live": True}
            ],
        }

    async def _browse(self, url: str, capability: str) -> dict[str, Any]:
        if self.executable is None or not self.executable.is_file():
            return self._unavailable(
                capability,
                "Yandex Browser executable is unavailable",
                url=url,
            )
        with tempfile.TemporaryDirectory(prefix="procurement-yandex-") as profile:
            command = [
                str(self.executable),
                "--headless",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-sync",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-download-notification",
                f"--user-data-dir={profile}",
                "--dump-dom",
                url,
            ]
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError:
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
                return self._unavailable(capability, "Yandex Browser timed out", "timeout", url)
            except OSError as exc:
                return self._unavailable(capability, f"Yandex Browser failed: {exc}", url=url)
        if len(stdout) > self.max_page_bytes:
            return self._unavailable(capability, "Browser page exceeded maximum size", url=url)
        if process.returncode != 0:
            return self._unavailable(
                capability,
                f"Yandex Browser exited with code {process.returncode}",
                url=url,
            )
        return {
            "status": "available",
            "capability": capability,
            "provider": "system_yandex",
            "browser_path": str(self.executable),
            "browser_version": self._version,
            "url": url,
            "live_data": False,
            "provenance": [],
            "_document": stdout.decode("utf-8", errors="replace"),
        }

    @staticmethod
    def _is_captcha(document: str) -> bool:
        folded = document.casefold()
        return "showcaptcha" in folded or "captcha__" in folded or "робот" in folded

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
            "provider": "system_yandex",
            "browser_path": str(self.executable) if self.executable else None,
            "browser_version": self._version,
            "url": url,
            "live_data": False,
            "provenance": [],
            "message": message,
        }


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
    "FixtureSupplierProvider",
    "SystemYandexBrowserProvider",
    "SupplierProvider",
    "WaitingBrowserProvider",
    "parse_yandex_results",
    "resolve_yandex_browser_path",
    "validate_public_url",
]
