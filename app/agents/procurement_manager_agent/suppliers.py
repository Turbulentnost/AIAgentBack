from __future__ import annotations

import hashlib
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol

from app.agents.procurement_agent.mcp_client import (
    MCPCallError,
    MCPUnavailableError,
    OneCMCPClient,
)
from app.agents.procurement_manager_agent.schemas import (
    Supplier,
    SupplierSearchRequest,
    SupplierSearchResult,
)

CONFIG_PATH = Path(__file__).with_name("supplier_mcp.json")


class SupplierSearchAdapter(Protocol):
    async def search(self, query: str, category: str | None, limit: int) -> list[Supplier]: ...


class SupplierMCPAdapter:
    """Adapter for the standalone procurement-supplier-mcp stdio server."""

    def __init__(self, client: OneCMCPClient | None = None) -> None:
        self.client = client or OneCMCPClient(
            config_path=CONFIG_PATH,
            server_name="supplier-read",
            timeout_seconds=120,
        )

    async def search(self, query: str, category: str | None, limit: int) -> list[Supplier]:
        response = await self.client.call_capability(
            "read_supplier_search",
            {"query": query, "category": category, "limit": limit},
        )
        if not isinstance(response, dict) or response.get("status") not in {
            "available",
            "partial",
        }:
            return []
        rows = response.get("items") or response.get("suppliers") or []
        return [_normalize_supplier(item) for item in rows if isinstance(item, dict)]


class FixtureSupplierAdapter:
    """Deterministic internal store used when a live 1C/RAG supplier index is unavailable."""

    def __init__(self, suppliers: list[Supplier] | None = None) -> None:
        self.suppliers = suppliers or [
            Supplier(
                supplier_id="fixture-steel",
                name="Проверенный поставщик металлопроката",
                source="internal",
                categories=["металл", "сталь", "прокат"],
                quality_rating=Decimal("92"),
                delivery_rating=Decimal("86"),
                commercial_rating=Decimal("80"),
                evidence=["internal_fixture"],
            ),
            Supplier(
                supplier_id="fixture-components",
                name="Поставщик промышленных комплектующих",
                source="internal",
                categories=["комплектующие", "подшипники", "крепёж"],
                quality_rating=Decimal("88"),
                delivery_rating=Decimal("90"),
                commercial_rating=Decimal("78"),
                evidence=["internal_fixture"],
            ),
        ]

    async def search(self, query: str, category: str | None, limit: int) -> list[Supplier]:
        terms = {part for part in f"{query} {category or ''}".casefold().split() if part}
        ranked = []
        for supplier in self.suppliers:
            haystack = " ".join([supplier.name, *supplier.categories]).casefold()
            score = sum(term in haystack for term in terms)
            if score:
                ranked.append((score, supplier))
        ranked.sort(key=lambda item: (-item[0], item[1].supplier_id))
        return [item[1] for item in ranked[:limit]]


class EmptyWebSupplierAdapter:
    """Explicit web fallback seam; production may inject a controlled search provider."""

    async def search(self, query: str, category: str | None, limit: int) -> list[Supplier]:
        _ = (query, category, limit)
        return []


class SupplierMCPWebAdapter:
    """Normalizes live Yandex MCP search results into supplier candidates."""

    def __init__(self, client: OneCMCPClient | None = None) -> None:
        self.client = client or OneCMCPClient(
            config_path=CONFIG_PATH,
            server_name="supplier-read",
            timeout_seconds=float(os.environ.get("YANDEX_BROWSER_REQUEST_TIMEOUT_SECONDS", "25")),
            max_attempts=1,
        )

    async def search(self, query: str, category: str | None, limit: int) -> list[Supplier]:
        search_query = " ".join(part for part in (query, category, "поставщик") if part)
        response = await self.client.call_capability(
            "search_supplier_web",
            {"query": search_query, "limit": limit},
        )
        if not isinstance(response, dict) or response.get("status") != "available":
            return []
        rows = response.get("items") or []
        return [
            _normalize_web_supplier(item)
            for item in rows
            if isinstance(item, dict) and item.get("url")
        ]


class HybridSupplierSearchService:
    def __init__(
        self,
        *,
        onec: SupplierSearchAdapter | None = None,
        internal: SupplierSearchAdapter | None = None,
        web: SupplierSearchAdapter | None = None,
        internal_threshold: int | None = None,
    ) -> None:
        self.onec = onec or SupplierMCPAdapter()
        self.internal = internal or FixtureSupplierAdapter()
        self.web = web or SupplierMCPWebAdapter()
        self.internal_threshold = max(
            1,
            internal_threshold
            or int(os.environ.get("PROCUREMENT_MANAGER_INTERNAL_SUPPLIER_THRESHOLD", "1")),
        )

    async def search_internal(self, request: SupplierSearchRequest) -> SupplierSearchResult:
        query = request.query or "поставщик"
        suppliers: list[Supplier] = []
        sources: list[str] = []
        try:
            suppliers.extend(
                await self.onec.search(query, request.category, request.limit)
            )
            if suppliers:
                sources.append("procurement_supplier_mcp")
        except (MCPUnavailableError, MCPCallError, OSError):
            pass

        internal = await self.internal.search(query, request.category, request.limit)
        if internal:
            suppliers.extend(internal)
            sources.append("internal")
        suppliers = _deduplicate(suppliers)[: request.limit]
        return SupplierSearchResult(
            query=query,
            suppliers=suppliers,
            sources_used=sources,
            web_fallback_used=False,
        )

    async def search_web(self, request: SupplierSearchRequest) -> SupplierSearchResult:
        query = request.query or "поставщик"
        suppliers: list[Supplier] = []
        sources: list[str] = []
        if request.allow_web_fallback:
            web_rows = await self.web.search(query, request.category, request.limit)
            suppliers = _deduplicate(web_rows)[: request.limit]
            if web_rows:
                sources.append("web")
        return SupplierSearchResult(
            query=query,
            suppliers=suppliers,
            sources_used=sources,
            web_fallback_used=request.allow_web_fallback,
        )

    async def search(self, request: SupplierSearchRequest) -> SupplierSearchResult:
        internal = await self.search_internal(request)
        if (
            len(internal.suppliers) >= self.internal_threshold
            or not request.allow_web_fallback
        ):
            return internal
        web = await self.search_web(request)
        return SupplierSearchResult(
            query=internal.query,
            suppliers=_deduplicate([*internal.suppliers, *web.suppliers])[: request.limit],
            sources_used=list(dict.fromkeys([*internal.sources_used, *web.sources_used])),
            web_fallback_used=True,
        )


def _normalize_supplier(
    raw: dict[str, Any],
) -> Supplier:
    supplier_id = str(raw.get("supplier_id") or raw.get("ref") or raw.get("Ref_Key") or "")
    raw_source = str(raw.get("source") or "internal")
    source: Literal["1c", "internal", "web"]
    if raw_source == "1c":
        source = "1c"
    elif raw_source == "web":
        source = "web"
    else:
        source = "internal"
    return Supplier(
        supplier_id=supplier_id,
        name=str(raw.get("name") or raw.get("Description") or supplier_id),
        tax_id=raw.get("tax_id") or raw.get("ИНН"),
        source=source,
        categories=list(raw.get("categories") or []),
        quality_rating=Decimal(str(raw.get("quality_rating") or 0)),
        delivery_rating=Decimal(str(raw.get("delivery_rating") or 0)),
        commercial_rating=Decimal(str(raw.get("commercial_rating") or 0)),
        is_active=bool(raw.get("is_active", True)),
        contacts=dict(raw.get("contacts") or {}),
        evidence=list(raw.get("evidence") or [f"{source}:{supplier_id}"]),
    )


def _normalize_web_supplier(raw: dict[str, Any]) -> Supplier:
    url = str(raw.get("url") or "")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    return Supplier(
        supplier_id=f"web-{digest}",
        name=str(raw.get("title") or url),
        source="web",
        categories=[],
        contacts={"website": url},
        evidence=[url],
    )


def _deduplicate(suppliers: list[Supplier]) -> list[Supplier]:
    result: dict[str, Supplier] = {}
    for supplier in suppliers:
        key = supplier.tax_id or supplier.supplier_id
        result.setdefault(key, supplier)
    return list(result.values())


__all__ = [
    "EmptyWebSupplierAdapter",
    "FixtureSupplierAdapter",
    "HybridSupplierSearchService",
    "SupplierMCPAdapter",
    "SupplierMCPWebAdapter",
    "SupplierSearchAdapter",
]
