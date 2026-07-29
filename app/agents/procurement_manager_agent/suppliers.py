from __future__ import annotations

import asyncio
import hashlib
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, Protocol

from app.agents.procurement_agent.mcp_client import (
    MCPCallError,
    MCPUnavailableError,
    OneCMCPClient,
)
from app.agents.procurement_manager_agent.schemas import (
    NomenclatureSearchItem,
    NomenclatureSupplierResult,
    Supplier,
    SupplierSearchRequest,
    SupplierSearchResult,
)
from app.agents.procurement_manager_agent.supplier_mcp_server.providers import (
    BrowserSearchProvider,
    build_default_browser_search_provider,
)
from app.agents.procurement_manager_agent.search_progress import (
    emit_progress,
    truncate_query,
)
from app.agents.procurement_manager_agent.web_page_enrichment import (
    derive_web_supplier_name,
    enrich_web_suppliers,
    run_qwen_browse_agent,
)
from app.agents.procurement_manager_agent.web_qwen import (
    clamp_agent_pages,
    qwen_agent_budget_seconds,
    qwen_agent_enabled,
    refine_search_query_with_qwen,
)

CONFIG_PATH = Path(__file__).with_name("supplier_mcp.json")

# Skip re-search when a nomenclature already has this many suppliers.
MIN_SUPPLIERS_BEFORE_SKIP = 3
# Hard cap for Edge/Bing (and MCP web) results per nomenclature.
WEB_LIMIT_PER_NOMENCLATURE = 5

# Generic tokens that appear in fixture names and must not block web fallback alone.
_FIXTURE_STOPWORDS = frozenset(
    {
        "поставщик",
        "поставщика",
        "поставщики",
        "supplier",
        "suppliers",
        "проверенный",
        "промышленных",
        "промышленный",
    }
)

_CITY_RE = re.compile(
    r"(?:г\.?\s*|город\s+|г\.о\.\s*)"
    r"([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё\-]+(?:[\s\-][A-ZА-ЯЁ][A-Za-zА-Яа-яЁё\-]+)?)"
    r"|([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё\-]+)\s*,\s*(?:РФ|Россия|обл)",
    re.UNICODE,
)
_PRICE_RE = re.compile(
    r"(?:от\s*)?"
    r"(\d{1,3}(?:[ \u00a0]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
    r"\s*(?:₽|руб\.?|RUB|р\.)",
    re.IGNORECASE,
)


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
        terms = {
            part
            for part in f"{query} {category or ''}".casefold().split()
            if part and part not in _FIXTURE_STOPWORDS
        }
        if not terms:
            return []
        ranked = []
        for supplier in self.suppliers:
            haystack = " ".join([supplier.name, *supplier.categories]).casefold()
            score = sum(term in haystack for term in terms)
            if score:
                ranked.append((score, supplier))
        ranked.sort(key=lambda item: (-item[0], item[1].supplier_id))
        return [_with_rating(item[1]) for item in ranked[:limit]]


class EmptyWebSupplierAdapter:
    """Explicit web fallback seam; production may inject a controlled search provider."""

    async def search(self, query: str, category: str | None, limit: int) -> list[Supplier]:
        _ = (query, category, limit)
        return []


class BrowserSupplierSearchAdapter:
    """In-process Edge/Chrome+Bing web search (main product path for UI buttons)."""

    def __init__(self, provider: BrowserSearchProvider | None = None) -> None:
        self.provider = provider or build_default_browser_search_provider()
        self.last_diagnostics: dict[str, Any] = {}

    async def search(self, query: str, category: str | None, limit: int) -> list[Supplier]:
        lead = (query or "").split(",")[0].strip() or (query or "")
        search_query = " ".join(part for part in (lead, category, "поставщик") if part)
        capped = max(1, min(limit, WEB_LIMIT_PER_NOMENCLATURE))
        response: dict[str, Any] | None = None
        last_error: str | None = None
        for _attempt in range(2):
            try:
                response = await self.provider.search(search_query, capped)
            except Exception as exc:
                response = None
                last_error = str(exc)[:500]
                continue
            if isinstance(response, dict) and response.get("status") == "available":
                break
        if not isinstance(response, dict) or response.get("status") != "available":
            status = (response or {}).get("status") if isinstance(response, dict) else None
            message = (
                (response or {}).get("message")
                if isinstance(response, dict)
                else None
            ) or last_error
            if status == "captcha":
                message = message or "Поисковик показал CAPTCHA — повторите или смените PROCUREMENT_WEB_SEARCH_ENGINE"
            elif status == "timeout":
                message = message or "Таймаут браузерного веб-поиска"
            elif not message:
                message = (
                    "Веб-поиск недоступен: HTTP SERP и Edge/Chrome не дали результатов. "
                    "Проверьте сеть, PROCUREMENT_WEB_SEARCH_PROVIDER и браузер."
                )
            self.last_diagnostics = {
                "status": status or "unavailable",
                "message": message,
                "query": search_query,
                "adapter": "browser",
            }
            return []
        rows = response.get("items") or []
        suppliers = [
            _normalize_web_supplier(item)
            for item in rows[:capped]
            if isinstance(item, dict) and item.get("url")
        ]
        self.last_diagnostics = {
            "status": "available",
            "message": None,
            "query": search_query,
            "count": len(suppliers),
            "adapter": "browser",
        }
        return suppliers

    async def fetch(self, url: str) -> dict[str, Any]:
        return await self.provider.fetch(url)


class SupplierMCPWebAdapter:
    """MCP stdio path for web search (optional; prefers in-process browser adapter)."""

    def __init__(self, client: OneCMCPClient | None = None) -> None:
        self.client = client or OneCMCPClient(
            config_path=CONFIG_PATH,
            server_name="supplier-read",
            timeout_seconds=float(
                os.environ.get(
                    "PROCUREMENT_WEB_BROWSER_TIMEOUT_SECONDS",
                    os.environ.get("YANDEX_BROWSER_REQUEST_TIMEOUT_SECONDS", "45"),
                )
            ),
            max_attempts=1,
        )

    async def search(self, query: str, category: str | None, limit: int) -> list[Supplier]:
        search_query = " ".join(part for part in (query, category, "поставщик") if part)
        capped = max(1, min(limit, WEB_LIMIT_PER_NOMENCLATURE))
        try:
            response = await self.client.call_capability(
                "search_supplier_web",
                {"query": search_query, "limit": capped},
            )
        except (MCPUnavailableError, MCPCallError, OSError):
            return []
        if not isinstance(response, dict) or response.get("status") != "available":
            return []
        rows = response.get("items") or []
        return [
            _normalize_web_supplier(item)
            for item in rows[:capped]
            if isinstance(item, dict) and item.get("url")
        ]


def _default_web_adapter() -> SupplierSearchAdapter:
    """Prefer in-process Edge/Bing; optional MCP-only mode via env."""
    mode = (os.environ.get("PROCUREMENT_WEB_ADAPTER") or "browser").strip().casefold()
    if mode in {"mcp", "stdio", "supplier_mcp"}:
        return SupplierMCPWebAdapter()
    return BrowserSupplierSearchAdapter()


class HybridSupplierSearchService:
    def __init__(
        self,
        *,
        onec: SupplierSearchAdapter | None = None,
        internal: SupplierSearchAdapter | None = None,
        web: SupplierSearchAdapter | None = None,
        internal_threshold: int | None = None,
        page_fetch_provider: BrowserSearchProvider | None = None,
    ) -> None:
        self.onec = onec or SupplierMCPAdapter()
        self.internal = internal or FixtureSupplierAdapter()
        self.web = web or _default_web_adapter()
        self.page_fetch_provider = page_fetch_provider
        self.internal_threshold = max(
            1,
            internal_threshold
            or int(os.environ.get("PROCUREMENT_MANAGER_INTERNAL_SUPPLIER_THRESHOLD", "1")),
        )
        self.last_agent_diagnostics: dict[str, Any] = {}
        self._agent_diag_lock = asyncio.Lock()

    def _with_web_diagnostics(self, result: SupplierSearchResult) -> SupplierSearchResult:
        """Attach browser + Qwen-agent diagnostics (keep SERP cards on soft failures)."""
        diagnostics = dict(getattr(self.web, "last_diagnostics", None) or {})
        agent_diag = dict(self.last_agent_diagnostics or {})
        if agent_diag:
            diagnostics = {**diagnostics, "qwen_browse_agent": agent_diag}
            agent_message = agent_diag.get("message")
            if agent_message and result.suppliers:
                # Surface agent status even when SERP returned cards.
                merged = dict(result.diagnostics or {})
                merged.update(diagnostics)
                return result.model_copy(
                    update={
                        "diagnostics": merged,
                        "message": result.message or agent_message,
                    }
                )
        if result.suppliers or not diagnostics:
            if result.diagnostics or result.message:
                if diagnostics and not result.diagnostics:
                    return result.model_copy(update={"diagnostics": diagnostics})
                return result
            return result
        message = diagnostics.get("message") or result.message
        status = "failed" if not result.suppliers and result.web_fallback_used else result.status
        return result.model_copy(
            update={
                "message": message,
                "diagnostics": diagnostics,
                "status": status if not result.suppliers else result.status,
            }
        )

    def _fetch_provider(self) -> BrowserSearchProvider | None:
        if self.page_fetch_provider is not None:
            return self.page_fetch_provider
        # Reuse in-process browser adapter when available (never invent a browser in tests).
        provider = getattr(self.web, "provider", None)
        if provider is not None and hasattr(provider, "fetch"):
            return provider
        return None

    async def _enrich_web(
        self,
        suppliers: list[Supplier],
        *,
        product_query: str | None = None,
        light: bool = False,
        deadline_monotonic: float | None = None,
    ) -> list[Supplier]:
        web_rows = [item for item in suppliers if item.source == "web" and _supplier_has_url(item)]
        if not web_rows:
            return suppliers
        provider = self._fetch_provider()
        if provider is None:
            return suppliers
        # Light mode: fewer pages, shorter fetch — keeps multi-nomenclature force_web
        # under the API timeout (full Qwen enrich is available via /enrich).
        enrich_kwargs: dict[str, Any] = {
            "product_query": product_query,
            "deadline_monotonic": deadline_monotonic,
        }
        if light:
            enrich_kwargs["max_pages"] = clamp_agent_pages(
                os.environ.get("PROCUREMENT_WEB_ENRICH_LIGHT_MAX_PAGES", "2")
            )
            enrich_kwargs["timeout_seconds"] = float(
                os.environ.get("PROCUREMENT_WEB_ENRICH_LIGHT_TIMEOUT_SECONDS", "10")
            )
            enrich_kwargs["concurrency"] = int(
                os.environ.get("PROCUREMENT_WEB_ENRICH_LIGHT_CONCURRENCY", "2")
            )
        enriched = await enrich_web_suppliers(web_rows, provider, **enrich_kwargs)
        by_id = {item.supplier_id: item for item in enriched}
        return [by_id.get(item.supplier_id, item) for item in suppliers]

    async def _browse_agent_web(
        self,
        suppliers: list[Supplier],
        *,
        product_query: str | None = None,
        deadline_monotonic: float | None = None,
    ) -> list[Supplier]:
        """Qwen browse+extract after SERP; fail-soft to unmodified SERP cards.

        Hard per-nomenclature budget: if Qwen/browser is slow, return SERP cards
        immediately so the outer force_web request does not time out empty.
        Also respects client alarm-clock deadline between page fetches.
        """
        web_rows = [item for item in suppliers if item.source == "web" and _supplier_has_url(item)]
        if not web_rows:
            return suppliers
        provider = self._fetch_provider()
        if provider is None:
            async with self._agent_diag_lock:
                self.last_agent_diagnostics = {
                    "qwen_agent": True,
                    "status": "no_fetch_provider",
                    "message": (
                        "Qwen-агент: нет browser/fetch провайдера, "
                        "карточки SERP без enrich"
                    ),
                }
            return suppliers
        budget = qwen_agent_budget_seconds()
        if deadline_monotonic is not None:
            remaining = float(deadline_monotonic) - asyncio.get_running_loop().time()
            if remaining <= 0:
                emit_progress("Время поиска истекло — показываю ссылки из поиска")
                return suppliers
            budget = min(budget, remaining)

        async def _run() -> tuple[list[Supplier], dict[str, Any]]:
            return await run_qwen_browse_agent(
                web_rows,
                provider,
                product_query=product_query,
                deadline_monotonic=deadline_monotonic,
            )

        try:
            enriched, diagnostics = await asyncio.wait_for(_run(), timeout=budget)
        except TimeoutError:
            emit_progress(
                "Qwen не успел обогатить карточки — показываю ссылки из поиска"
            )
            async with self._agent_diag_lock:
                previous = dict(self.last_agent_diagnostics or {})
                runs = list(previous.get("runs") or [])
                runs.append(
                    {
                        "query": (product_query or "")[:160],
                        "status": "budget_timeout",
                        "budget_seconds": budget,
                    }
                )
                self.last_agent_diagnostics = {
                    "qwen_agent": True,
                    "status": "budget_timeout",
                    "budget_seconds": budget,
                    "runs": runs[-20:],
                    "nomenclature_runs": len(runs),
                    "message": (
                        f"Qwen-агент: бюджет {budget:.0f}с исчерпан, "
                        "возвращены карточки SERP"
                    ),
                }
            return suppliers
        except Exception as exc:
            async with self._agent_diag_lock:
                self.last_agent_diagnostics = {
                    "qwen_agent": True,
                    "status": "error",
                    "error": type(exc).__name__,
                    "message": "Qwen-агент: ошибка, возвращены карточки SERP",
                }
            return suppliers
        # Merge per-nomenclature agent stats under a lock (parallel nomenclatures).
        async with self._agent_diag_lock:
            previous = dict(self.last_agent_diagnostics or {})
            runs = list(previous.get("runs") or [])
            runs.append(
                {
                    "query": (product_query or "")[:160],
                    **{
                        key: diagnostics.get(key)
                        for key in (
                            "status",
                            "visited",
                            "qwen_enriched",
                            "selected_indexes",
                            "qwen_skip_reason",
                        )
                    },
                }
            )
            total_visited = sum(int(run.get("visited") or 0) for run in runs)
            total_qwen = sum(int(run.get("qwen_enriched") or 0) for run in runs)
            self.last_agent_diagnostics = {
                **diagnostics,
                "runs": runs[-20:],
                "nomenclature_runs": len(runs),
                "visited": total_visited,
                "qwen_enriched": total_qwen,
                "message": (
                    f"Qwen-агент: номенклатур={len(runs)}, открыто стр.={total_visited}, "
                    f"Qwen-полей={total_qwen}"
                ),
            }
        by_id = {item.supplier_id: item for item in enriched}
        return [by_id.get(item.supplier_id, item) for item in suppliers]

    async def _track_existing_links(
        self,
        suppliers: list[Supplier],
        *,
        product_query: str | None = None,
        do_enrich: bool | Literal["light", "agent"] = True,
        deadline_monotonic: float | None = None,
    ) -> list[Supplier]:
        """Visit/enrich prior candidate URLs before a cold SERP search."""
        link_seeds = existing_link_suppliers(suppliers)[:WEB_LIMIT_PER_NOMENCLATURE]
        if not link_seeds:
            return []
        emit_progress(f"Обработка {len(link_seeds)} существующих ссылок…")
        if (
            deadline_monotonic is not None
            and asyncio.get_running_loop().time() >= deadline_monotonic
        ):
            emit_progress("Время поиска истекло — оставляю существующие ссылки без обновления")
            return link_seeds
        if not do_enrich:
            return link_seeds
        if do_enrich == "agent":
            return await self._browse_agent_web(
                link_seeds,
                product_query=product_query,
                deadline_monotonic=deadline_monotonic,
            )
        return await self._enrich_web(
            link_seeds,
            product_query=product_query,
            light=(do_enrich == "light"),
            deadline_monotonic=deadline_monotonic,
        )

    async def search_internal(self, request: SupplierSearchRequest) -> SupplierSearchResult:
        targets = resolve_nomenclature_targets(request) or [_fallback_target(request)]
        force_web = request.is_manual_web

        async def _one(target: NomenclatureSearchItem) -> NomenclatureSupplierResult:
            existing = list(target.existing_suppliers or [])
            query = (
                target.query or target.nomenclature_name or request.query or "поставщик"
            ).strip()
            qualifying = qualifying_suppliers_for_skip(existing, force_web=force_web)
            if len(qualifying) >= MIN_SUPPLIERS_BEFORE_SKIP:
                return NomenclatureSupplierResult(
                    nomenclature_id=target.nomenclature_id,
                    nomenclature_name=target.nomenclature_name,
                    query=query,
                    suppliers=_deduplicate(existing)[: request.limit],
                    sources_used=["existing"],
                    web_fallback_used=False,
                )
            single = await self._search_internal_one(query, request)
            suppliers = _deduplicate([*existing, *single.suppliers])[: request.limit]
            sources = list(single.sources_used)
            if existing:
                sources = list(dict.fromkeys(["existing", *sources]))
            return NomenclatureSupplierResult(
                nomenclature_id=target.nomenclature_id,
                nomenclature_name=target.nomenclature_name,
                query=query,
                suppliers=suppliers,
                sources_used=sources,
                web_fallback_used=False,
            )

        return _aggregate(list(await asyncio.gather(*[_one(item) for item in targets])), request)

    async def search_web(self, request: SupplierSearchRequest) -> SupplierSearchResult:
        targets = resolve_nomenclature_targets(request) or [_fallback_target(request)]
        force_web = request.is_manual_web
        self.last_agent_diagnostics = {}
        deadline_monotonic: float | None = None
        if request.timeout_seconds is not None:
            deadline_monotonic = (
                asyncio.get_running_loop().time()
                + max(30.0, min(600.0, float(request.timeout_seconds)))
            )
        # Cap parallelism: each item may open browsers / hit LLM; stampede → API timeout.
        nom_concurrency = max(
            1,
            int(os.environ.get("PROCUREMENT_WEB_NOMENCLATURE_CONCURRENCY", "2")),
        )
        semaphore = asyncio.Semaphore(nom_concurrency)
        # Manual «Найти поставщиков»: Qwen browse agent (default) visits top SERP URLs.
        # PROCUREMENT_WEB_ENRICH_ON_MANUAL_SEARCH=skip disables enrich; =full uses classic enrich.
        enrich_mode = (
            os.environ.get("PROCUREMENT_WEB_ENRICH_ON_MANUAL_SEARCH") or "auto"
        ).strip().casefold()
        agent_on = qwen_agent_enabled()
        if force_web:
            if enrich_mode in {"0", "false", "no", "off", "skip"}:
                do_enrich: bool | Literal["light", "agent"] = False
            elif enrich_mode in {"1", "true", "yes", "on", "full"}:
                do_enrich = "agent" if agent_on else True
            elif enrich_mode in {"agent", "browse"}:
                do_enrich = "agent"
            elif enrich_mode == "light":
                do_enrich = "light"
            else:
                # auto: prefer Qwen browse agent for force_web (incl. multi-item);
                # without agent — single nomen full enrich, multi SERP-only (+ /enrich).
                if agent_on:
                    do_enrich = "agent"
                else:
                    do_enrich = True if len(targets) <= 1 else False
        else:
            do_enrich = True

        async def _one(target: NomenclatureSearchItem) -> NomenclatureSupplierResult:
            async with semaphore:
                existing = list(target.existing_suppliers or [])
                query = (
                    target.query or target.nomenclature_name or request.query or "поставщик"
                ).strip()
                label = truncate_query(
                    target.nomenclature_name or query, max_len=56
                )
                if (
                    deadline_monotonic is not None
                    and asyncio.get_running_loop().time() >= deadline_monotonic
                ):
                    emit_progress(f"Время поиска истекло — пропуск: {label}")
                    return NomenclatureSupplierResult(
                        nomenclature_id=target.nomenclature_id,
                        nomenclature_name=target.nomenclature_name,
                        query=query,
                        suppliers=[],
                        sources_used=[],
                        web_fallback_used=force_web,
                    )

                # Prefer prior URLs (workspace cards / last search) before cold SERP.
                tracked_links = await self._track_existing_links(
                    existing,
                    product_query=query,
                    do_enrich=do_enrich,
                    deadline_monotonic=deadline_monotonic,
                )
                if tracked_links:
                    existing = _deduplicate([*tracked_links, *existing])

                # Manual web UI: web-only cards; SERP only if tracked links are insufficient.
                if force_web:
                    strong_tracked = qualifying_suppliers_for_skip(
                        tracked_links, force_web=True
                    )
                    if len(strong_tracked) >= MIN_SUPPLIERS_BEFORE_SKIP:
                        web_only = [
                            item
                            for item in tracked_links
                            if item.source == "web" and _supplier_has_url(item)
                        ][:WEB_LIMIT_PER_NOMENCLATURE]
                        emit_progress(
                            f"Готово по номенклатуре {label}: "
                            f"{len(web_only)} поставщиков (существующие ссылки)"
                        )
                        return NomenclatureSupplierResult(
                            nomenclature_id=target.nomenclature_id,
                            nomenclature_name=target.nomenclature_name,
                            query=query,
                            suppliers=web_only,
                            sources_used=["existing", "web"] if web_only else ["existing"],
                            web_fallback_used=False,
                        )

                    if (
                        deadline_monotonic is not None
                        and asyncio.get_running_loop().time() >= deadline_monotonic
                    ):
                        web_only = [
                            item
                            for item in tracked_links
                            if item.source == "web" and _supplier_has_url(item)
                        ][:WEB_LIMIT_PER_NOMENCLATURE]
                        emit_progress(
                            f"Время поиска истекло — показываю {len(web_only)} "
                            "обработанных ссылок"
                        )
                        return NomenclatureSupplierResult(
                            nomenclature_id=target.nomenclature_id,
                            nomenclature_name=target.nomenclature_name,
                            query=query,
                            suppliers=web_only,
                            sources_used=["existing", "web"] if web_only else [],
                            web_fallback_used=False,
                        )

                    emit_progress(f"Ищу в интернете: {truncate_query(query)}")
                    single = await self._search_web_one(query, request)
                    web_rows = [
                        item
                        for item in single.suppliers
                        if item.source == "web"
                    ][:WEB_LIMIT_PER_NOMENCLATURE]
                    emit_progress(f"Нашёл {len(web_rows)} ссылок")
                    if do_enrich and web_rows:
                        if do_enrich == "agent":
                            serp_rows = await self._browse_agent_web(
                                web_rows,
                                product_query=query,
                                deadline_monotonic=deadline_monotonic,
                            )
                        else:
                            serp_rows = await self._enrich_web(
                                web_rows,
                                product_query=query,
                                light=(do_enrich == "light"),
                                deadline_monotonic=deadline_monotonic,
                            )
                    else:
                        serp_rows = web_rows
                    web_only = _deduplicate([*tracked_links, *serp_rows])[
                        :WEB_LIMIT_PER_NOMENCLATURE
                    ]
                    web_only = [
                        item
                        for item in web_only
                        if item.source == "web" and _supplier_has_url(item)
                    ][:WEB_LIMIT_PER_NOMENCLATURE]
                    sources = ["web"] if serp_rows else list(single.sources_used)
                    if tracked_links:
                        sources = list(dict.fromkeys(["existing", *sources]))
                    emit_progress(
                        f"Готово по номенклатуре {label}: {len(web_only)} поставщиков"
                    )
                    return NomenclatureSupplierResult(
                        nomenclature_id=target.nomenclature_id,
                        nomenclature_name=target.nomenclature_name,
                        query=single.query or query,
                        suppliers=web_only,
                        sources_used=sources,
                        web_fallback_used=bool(serp_rows),
                    )

                # NOTE: force_web diagnostics attached on aggregate via adapter.last_diagnostics
                qualifying = qualifying_suppliers_for_skip(existing, force_web=force_web)
                if len(qualifying) >= MIN_SUPPLIERS_BEFORE_SKIP:
                    return NomenclatureSupplierResult(
                        nomenclature_id=target.nomenclature_id,
                        nomenclature_name=target.nomenclature_name,
                        query=query,
                        suppliers=_deduplicate(existing)[: request.limit],
                        sources_used=["existing"],
                        web_fallback_used=False,
                    )
                single = await self._search_web_one(query, request)
                web_rows = await self._enrich_web(
                    list(single.suppliers),
                    product_query=query,
                    deadline_monotonic=deadline_monotonic,
                )
                suppliers = _deduplicate([*existing, *web_rows])[
                    : max(request.limit, WEB_LIMIT_PER_NOMENCLATURE)
                ]
                sources = list(single.sources_used)
                if existing or tracked_links:
                    sources = list(dict.fromkeys(["existing", *sources]))
                return NomenclatureSupplierResult(
                    nomenclature_id=target.nomenclature_id,
                    nomenclature_name=target.nomenclature_name,
                    query=query,
                    suppliers=suppliers,
                    sources_used=sources,
                    web_fallback_used=single.web_fallback_used,
                )

        aggregated = _aggregate(
            list(await asyncio.gather(*[_one(item) for item in targets])), request
        )
        return self._with_web_diagnostics(aggregated)

    async def search(self, request: SupplierSearchRequest) -> SupplierSearchResult:
        targets = resolve_nomenclature_targets(request)
        if not targets:
            targets = [_fallback_target(request)]
        force_web = request.is_manual_web
        # Manual «Найти поставщиков»: web-only enriched cards (no internal bank tiles).
        if force_web:
            return self._with_web_diagnostics(await self.search_web(request))

        async def _one(target: NomenclatureSearchItem) -> NomenclatureSupplierResult:
            existing = list(target.existing_suppliers or [])
            query = (
                target.query or target.nomenclature_name or request.query or "поставщик"
            ).strip()
            qualifying_existing = qualifying_suppliers_for_skip(
                existing, force_web=force_web
            )
            # Bank-only seeds must not block manual web; auto still skips on ≥3 real matches.
            if len(qualifying_existing) >= MIN_SUPPLIERS_BEFORE_SKIP:
                return NomenclatureSupplierResult(
                    nomenclature_id=target.nomenclature_id,
                    nomenclature_name=target.nomenclature_name,
                    query=query,
                    suppliers=_deduplicate(existing)[: request.limit],
                    sources_used=["existing"],
                    web_fallback_used=False,
                )

            internal = await self._search_internal_one(query, request)
            suppliers = _deduplicate([*existing, *internal.suppliers])
            sources = list(internal.sources_used)
            if existing:
                sources = list(dict.fromkeys(["existing", *sources]))
            web_used = False
            need_web = False
            if request.allow_web_fallback:
                substantive = [
                    item for item in suppliers if not _is_fixture_supplier(item)
                ]
                # Bank seeds without links do not satisfy the internal threshold.
                substantive = qualifying_suppliers_for_skip(
                    substantive, force_web=False
                )
                need_web = len(substantive) < self.internal_threshold
            if need_web:
                # Track prior URLs first; only cold-SERP when still below threshold.
                tracked_links = await self._track_existing_links(
                    suppliers,
                    product_query=query,
                    do_enrich=True,
                )
                if tracked_links:
                    suppliers = _deduplicate([*tracked_links, *suppliers])
                    sources = list(dict.fromkeys(["existing", *sources]))
                    substantive = qualifying_suppliers_for_skip(
                        [item for item in suppliers if not _is_fixture_supplier(item)],
                        force_web=False,
                    )
                    need_web = len(substantive) < self.internal_threshold
            if need_web:
                web = await self._search_web_one(query, request)
                web_rows = await self._enrich_web(
                    list(web.suppliers),
                    product_query=query,
                )
                suppliers = _deduplicate([*suppliers, *web_rows])
                sources = list(dict.fromkeys([*sources, *web.sources_used]))
                web_used = True
            return NomenclatureSupplierResult(
                nomenclature_id=target.nomenclature_id,
                nomenclature_name=target.nomenclature_name,
                query=query,
                suppliers=suppliers[: max(request.limit, WEB_LIMIT_PER_NOMENCLATURE)],
                sources_used=sources,
                web_fallback_used=web_used,
            )

        results = list(await asyncio.gather(*[_one(item) for item in targets]))
        return _aggregate(results, request)

    async def _search_internal_one(
        self, query: str, request: SupplierSearchRequest
    ) -> SupplierSearchResult:
        q = query or "поставщик"
        suppliers: list[Supplier] = []
        sources: list[str] = []
        try:
            suppliers.extend(await self.onec.search(q, request.category, request.limit))
            if suppliers:
                sources.append("procurement_supplier_mcp")
        except (MCPUnavailableError, MCPCallError, OSError):
            pass

        internal = await self.internal.search(q, request.category, request.limit)
        if internal:
            suppliers.extend(internal)
            sources.append("internal")
        suppliers = [_with_rating(item) for item in _deduplicate(suppliers)[: request.limit]]
        return SupplierSearchResult(
            query=q,
            suppliers=suppliers,
            sources_used=sources,
            web_fallback_used=False,
        )

    async def _search_web_one(
        self, query: str, request: SupplierSearchRequest
    ) -> SupplierSearchResult:
        q = query or "поставщик"
        # Optional Qwen refine (short Russian Bing query); falls back to original.
        try:
            q = await refine_search_query_with_qwen(q) or q
        except Exception:
            pass
        suppliers: list[Supplier] = []
        sources: list[str] = []
        diagnostics: dict[str, Any] = {}
        message: str | None = None
        if request.allow_web_fallback:
            web_limit = min(request.limit, WEB_LIMIT_PER_NOMENCLATURE)
            web_rows = await self.web.search(q, request.category, web_limit)
            suppliers = _deduplicate(web_rows)[:web_limit]
            if web_rows:
                sources.append("web")
            diagnostics = dict(getattr(self.web, "last_diagnostics", None) or {})
            if not web_rows:
                message = diagnostics.get("message") or (
                    "Веб-поиск не вернул поставщиков. Проверьте браузер и сеть."
                )
        return SupplierSearchResult(
            query=q,
            suppliers=suppliers,
            sources_used=sources,
            web_fallback_used=request.allow_web_fallback,
            message=message,
            diagnostics=diagnostics,
        )


def resolve_nomenclature_targets(
    request: SupplierSearchRequest,
) -> list[NomenclatureSearchItem]:
    """Build per-nomenclature search targets from request.nomenclatures or query splits."""
    if request.nomenclatures:
        targets: list[NomenclatureSearchItem] = []
        for item in request.nomenclatures:
            query = (
                (item.query or item.nomenclature_name or item.nomenclature_id or "")
                .strip()
            )
            if len(query) < 2:
                continue
            targets.append(
                NomenclatureSearchItem(
                    nomenclature_id=item.nomenclature_id,
                    nomenclature_name=item.nomenclature_name or query,
                    query=query[:500],
                    existing_suppliers=list(item.existing_suppliers or []),
                )
            )
        if targets:
            return targets

    raw = (request.query or "").strip()
    if not raw:
        return []
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(parts) <= 1:
        return [
            NomenclatureSearchItem(
                nomenclature_id=None,
                nomenclature_name=raw,
                query=raw[:500],
            )
        ]
    return [
        NomenclatureSearchItem(
            nomenclature_id=None,
            nomenclature_name=part,
            query=part[:500],
        )
        for part in parts
        if len(part) >= 2
    ]


def _fallback_target(request: SupplierSearchRequest) -> NomenclatureSearchItem:
    query = (request.query or "поставщик").strip()[:500]
    return NomenclatureSearchItem(
        nomenclature_id=None,
        nomenclature_name=query,
        query=query,
    )


def _wrap_single(
    target: NomenclatureSearchItem, result: SupplierSearchResult
) -> NomenclatureSupplierResult:
    return NomenclatureSupplierResult(
        nomenclature_id=target.nomenclature_id,
        nomenclature_name=target.nomenclature_name or result.query,
        query=result.query,
        suppliers=result.suppliers,
        sources_used=result.sources_used,
        web_fallback_used=result.web_fallback_used,
    )


def _aggregate(
    rows: list[NomenclatureSupplierResult],
    request: SupplierSearchRequest,
) -> SupplierSearchResult:
    flat: list[Supplier] = []
    sources: list[str] = []
    web_used = False
    queries: list[str] = []
    for row in rows:
        flat.extend(row.suppliers)
        sources.extend(row.sources_used)
        web_used = web_used or row.web_fallback_used
        if row.query:
            queries.append(row.query)
    combined_query = ", ".join(dict.fromkeys(queries))[:500] or (
        request.query or "поставщик"
    )
    return SupplierSearchResult(
        query=combined_query,
        suppliers=_deduplicate(flat)[: max(request.limit, WEB_LIMIT_PER_NOMENCLATURE) * max(1, len(rows))],
        sources_used=list(dict.fromkeys(sources)),
        web_fallback_used=web_used,
        nomenclature_results=rows,
    )


def _is_fixture_supplier(supplier: Supplier) -> bool:
    if supplier.supplier_id.startswith("fixture-"):
        return True
    return any(
        evidence in {"internal_fixture"} or str(evidence).startswith("fixture:")
        for evidence in supplier.evidence
    )


def _supplier_has_url(supplier: Supplier) -> bool:
    return bool(supplier.url or supplier.contacts.get("website"))


def _supplier_url_key(supplier: Supplier) -> str:
    raw = (supplier.url or supplier.contacts.get("website") or "").strip()
    return raw.casefold()


def _as_trackable_web_supplier(supplier: Supplier) -> Supplier:
    """Normalize prior cards with a URL so enrich/browse agents will open them."""
    url = (supplier.url or supplier.contacts.get("website") or "").strip() or None
    contacts = dict(supplier.contacts or {})
    if url and not contacts.get("website"):
        contacts["website"] = url
    updates: dict[str, Any] = {"contacts": contacts}
    if url and supplier.url != url:
        updates["url"] = url
    if supplier.source != "web":
        updates["source"] = "web"
    evidence = list(supplier.evidence or [])
    if "existing_link" not in evidence:
        evidence.append("existing_link")
        updates["evidence"] = evidence
    if not updates:
        return supplier
    return supplier.model_copy(update=updates)


def existing_link_suppliers(suppliers: list[Supplier]) -> list[Supplier]:
    """Prior/candidate cards that already carry a product/shop URL to track first."""
    seen_url: set[str] = set()
    seen_id: set[str] = set()
    result: list[Supplier] = []
    for supplier in suppliers:
        if not _supplier_has_url(supplier):
            continue
        url_key = _supplier_url_key(supplier)
        sid = supplier.tax_id or supplier.supplier_id
        if (url_key and url_key in seen_url) or (sid and sid in seen_id):
            continue
        if url_key:
            seen_url.add(url_key)
        if sid:
            seen_id.add(sid)
        result.append(_as_trackable_web_supplier(supplier))
    return result


def _is_bank_seed_supplier(supplier: Supplier) -> bool:
    return any(str(evidence).startswith("bank:") for evidence in supplier.evidence)


def qualifying_suppliers_for_skip(
    suppliers: list[Supplier],
    *,
    force_web: bool = False,
) -> list[Supplier]:
    """Suppliers that count toward skip-if-≥3 (and toward web-need checks).

    Manual ``force_web`` / ``manual_web``: only 1C or web-with-URL count.
    Bank fixture seeds without links never block live web search.
    Auto mode: non-fixture suppliers count, except bank seeds without URL.
    """
    result: list[Supplier] = []
    for supplier in suppliers:
        if _is_fixture_supplier(supplier):
            continue
        has_url = _supplier_has_url(supplier)
        if force_web:
            if supplier.source == "1c":
                result.append(supplier)
            elif supplier.source == "web" and has_url:
                result.append(supplier)
            continue
        if _is_bank_seed_supplier(supplier) and not has_url:
            continue
        result.append(supplier)
    return result


def _rating_from_parts(supplier: Supplier) -> Decimal | None:
    parts = [
        value
        for value in (
            supplier.quality_rating,
            supplier.delivery_rating,
            supplier.commercial_rating,
        )
        if value and value > 0
    ]
    if not parts:
        return supplier.rating
    return (sum(parts, Decimal("0")) / Decimal(len(parts))).quantize(Decimal("0.01"))


def _with_rating(supplier: Supplier) -> Supplier:
    rating = supplier.rating if supplier.rating is not None else _rating_from_parts(supplier)
    cost = supplier.approx_cost if supplier.approx_cost is not None else supplier.unit_price
    url = supplier.url or supplier.contacts.get("website")
    if (
        rating == supplier.rating
        and cost == supplier.approx_cost
        and url == supplier.url
    ):
        return supplier
    return supplier.model_copy(
        update={
            "rating": rating,
            "approx_cost": cost,
            "unit_price": supplier.unit_price if supplier.unit_price is not None else cost,
            "url": url,
        }
    )


def _normalize_supplier(raw: dict[str, Any]) -> Supplier:
    supplier_id = str(raw.get("supplier_id") or raw.get("ref") or raw.get("Ref_Key") or "")
    raw_source = str(raw.get("source") or "internal")
    source: Literal["1c", "internal", "web"]
    if raw_source == "1c":
        source = "1c"
    elif raw_source == "web":
        source = "web"
    else:
        source = "internal"
    contacts = dict(raw.get("contacts") or {})
    url = raw.get("url") or raw.get("link") or contacts.get("website")
    if url:
        contacts.setdefault("website", str(url))
    unit_price = _to_decimal(raw.get("unit_price") or raw.get("approx_cost") or raw.get("price"))
    rating = _to_decimal(raw.get("rating") or raw.get("score"))
    abc_raw = str(raw.get("abc_class") or "").strip().upper()
    abc_class = abc_raw if abc_raw in {"A", "B", "C"} else None
    abc_share = _to_decimal(raw.get("abc_spend_share"))
    supplier = Supplier(
        supplier_id=supplier_id,
        name=str(raw.get("name") or raw.get("Description") or supplier_id),
        tax_id=raw.get("tax_id") or raw.get("ИНН"),
        source=source,
        categories=list(raw.get("categories") or []),
        quality_rating=Decimal(str(raw.get("quality_rating") or 0)),
        delivery_rating=Decimal(str(raw.get("delivery_rating") or 0)),
        commercial_rating=Decimal(str(raw.get("commercial_rating") or 0)),
        is_active=bool(raw.get("is_active", True)),
        contacts=contacts,
        evidence=list(raw.get("evidence") or [f"{source}:{supplier_id}"]),
        url=str(url) if url else None,
        city=str(raw["city"]) if raw.get("city") else None,
        unit_price=unit_price,
        approx_cost=unit_price,
        rating=rating,
        abc_class=abc_class,  # type: ignore[arg-type]
        abc_spend_share=abc_share if abc_share is not None and abc_share <= 1 else None,
    )
    return _with_rating(supplier)


def _normalize_web_supplier(raw: dict[str, Any]) -> Supplier:
    url = str(raw.get("url") or "")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    snippet = str(raw.get("snippet") or raw.get("description") or "")
    city = str(raw["city"]) if raw.get("city") else _extract_city(snippet)
    price = _to_decimal(raw.get("unit_price") or raw.get("price")) or _extract_price(snippet)
    score = _to_decimal(raw.get("rating") or raw.get("score"))
    title = raw.get("title")
    name = raw.get("name")
    display_name = derive_web_supplier_name(
        url=url,
        title=str(title) if title else None,
        name=str(name) if name else None,
        shop_name=str(raw["shop_name"]) if raw.get("shop_name") else None,
        site_name=str(raw["site_name"]) if raw.get("site_name") else None,
    )
    return Supplier(
        supplier_id=f"web-{digest}",
        name=display_name,
        source="web",
        categories=[],
        contacts={"website": url},
        evidence=[url] + ([snippet[:240]] if snippet else []),
        url=url,
        city=city,
        unit_price=price,
        approx_cost=price,
        rating=score,
    )


def _extract_city(snippet: str) -> str | None:
    if not snippet:
        return None
    match = _CITY_RE.search(snippet)
    if not match:
        return None
    return (match.group(1) or match.group(2) or "").strip() or None


def _extract_price(snippet: str) -> Decimal | None:
    if not snippet:
        return None
    match = _PRICE_RE.search(snippet)
    if not match:
        return None
    raw = match.group(1).replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    return _to_decimal(raw)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _deduplicate(suppliers: list[Supplier]) -> list[Supplier]:
    result: dict[str, Supplier] = {}
    for supplier in suppliers:
        key = supplier.tax_id or supplier.supplier_id
        existing = result.get(key)
        if existing is None:
            result[key] = _with_rating(supplier)
            continue
        # Prefer richer card when merging duplicates.
        merged = existing.model_copy(
            update={
                "url": existing.url or supplier.url,
                "city": existing.city or supplier.city,
                "unit_price": existing.unit_price
                if existing.unit_price is not None
                else supplier.unit_price,
                "approx_cost": existing.approx_cost
                if existing.approx_cost is not None
                else supplier.approx_cost,
                "rating": existing.rating if existing.rating is not None else supplier.rating,
                "evidence": list(dict.fromkeys([*existing.evidence, *supplier.evidence])),
            }
        )
        result[key] = _with_rating(merged)
    return list(result.values())


__all__ = [
    "BrowserSupplierSearchAdapter",
    "EmptyWebSupplierAdapter",
    "FixtureSupplierAdapter",
    "HybridSupplierSearchService",
    "MIN_SUPPLIERS_BEFORE_SKIP",
    "SupplierMCPAdapter",
    "SupplierMCPWebAdapter",
    "SupplierSearchAdapter",
    "WEB_LIMIT_PER_NOMENCLATURE",
    "existing_link_suppliers",
    "qualifying_suppliers_for_skip",
    "resolve_nomenclature_targets",
]
