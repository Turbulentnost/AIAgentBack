from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.agents.procurement_manager_agent.operations import MutationGate
from app.agents.procurement_manager_agent.schemas import (
    ApprovalRecord,
    NomenclatureSearchItem,
    QuoteLine,
    Supplier,
    SupplierQuote,
    SupplierSearchRequest,
)
from app.agents.procurement_manager_agent.scoring import compare_quotes
from app.agents.procurement_manager_agent.suppliers import (
    WEB_LIMIT_PER_NOMENCLATURE,
    BrowserSupplierSearchAdapter,
    FixtureSupplierAdapter,
    HybridSupplierSearchService,
    SupplierSearchAdapter,
)


def _quote(
    quote_id: str,
    *,
    price: str,
    days: int,
    quality: str,
    risk: str,
) -> SupplierQuote:
    return SupplierQuote(
        quote_id=quote_id,
        supplier_id=f"supplier-{quote_id}",
        lines=[
            QuoteLine(
                line_id="line-1",
                unit_price=Decimal(price),
                quantity=Decimal("10"),
                delivery_days=days,
            )
        ],
        quality_score=Decimal(quality),
        risk_score=Decimal(risk),
    )


def test_quote_scoring_is_deterministic() -> None:
    quotes = [
        _quote("cheap", price="10", days=10, quality="80", risk="10"),
        _quote("fast", price="12", days=2, quality="95", risk="5"),
    ]
    first = compare_quotes(quotes)
    second = compare_quotes(list(reversed(quotes)))
    assert first.recommended_quote_id == second.recommended_quote_id
    assert [(row.quote_id, row.final_score) for row in first.scores] == [
        (row.quote_id, row.final_score) for row in second.scores
    ]


def test_mutation_gate_requires_approved_id_and_forbids_payment() -> None:
    from datetime import UTC, datetime

    approval = ApprovalRecord(
        approval_id="approval-1",
        operation="create_supplier_order",
        status="approved",
        created_at=datetime.now(UTC),
    )
    assert (
        MutationGate.authorize("create_supplier_order", "approval-1", [approval]).approval_id
        == "approval-1"
    )
    with pytest.raises(PermissionError, match="approval_id"):
        MutationGate.authorize("create_supplier_order", None, [approval])
    with pytest.raises(PermissionError, match="Payment"):
        MutationGate.authorize("execute_payment", "approval-1", [approval])


class _Adapter(SupplierSearchAdapter):
    def __init__(self, rows: list[Supplier] | None = None, *, fail: bool = False) -> None:
        self.rows = rows or []
        self.fail = fail
        self.calls = 0
        self.queries: list[str] = []
        self.limits: list[int] = []

    async def search(self, query: str, category: str | None, limit: int) -> list[Supplier]:
        _ = category
        self.calls += 1
        self.queries.append(query)
        self.limits.append(limit)
        if self.fail:
            from app.agents.procurement_agent.mcp_client import MCPUnavailableError

            raise MCPUnavailableError("offline")
        return self.rows


@pytest.mark.asyncio
async def test_web_search_is_only_used_when_internal_sources_are_empty() -> None:
    onec = _Adapter(fail=True)
    internal = _Adapter([Supplier(supplier_id="internal-1", name="Internal")])
    web = _Adapter([Supplier(supplier_id="web-1", name="Web", source="web")])
    service = HybridSupplierSearchService(onec=onec, internal=internal, web=web)

    result = await service.search(
        SupplierSearchRequest(query="steel", idempotency_key="search-1")
    )

    assert [item.supplier_id for item in result.suppliers] == ["internal-1"]
    assert web.calls == 0
    assert result.web_fallback_used is False


@pytest.mark.asyncio
async def test_web_fallback_runs_after_empty_internal_search() -> None:
    web = _Adapter([Supplier(supplier_id="web-1", name="Web", source="web")])
    service = HybridSupplierSearchService(
        onec=_Adapter(fail=True),
        internal=_Adapter(),
        web=web,
    )
    result = await service.search(
        SupplierSearchRequest(query="rare item", idempotency_key="search-2")
    )
    assert web.calls == 1
    assert result.web_fallback_used is True
    assert result.sources_used == ["web"]


@pytest.mark.asyncio
async def test_fixture_stopwords_alone_do_not_block_web_fallback() -> None:
    web = _Adapter([Supplier(supplier_id="web-1", name="Web", source="web")])
    service = HybridSupplierSearchService(
        onec=_Adapter(fail=True),
        internal=FixtureSupplierAdapter(),
        web=web,
    )
    result = await service.search(
        SupplierSearchRequest(query="поставщик", idempotency_key="search-stopwords")
    )
    assert web.calls == 1
    assert result.web_fallback_used is True
    assert result.sources_used == ["web"]


@pytest.mark.asyncio
async def test_fixture_hits_do_not_suppress_web_fallback() -> None:
    web = _Adapter([Supplier(supplier_id="web-1", name="Web", source="web")])
    service = HybridSupplierSearchService(
        onec=_Adapter(
            [
                Supplier(
                    supplier_id="fixture-steel",
                    name="Проверенный поставщик металлопроката",
                    evidence=["fixture:supplier:fixture-steel"],
                )
            ]
        ),
        internal=_Adapter(),
        web=web,
    )
    result = await service.search(
        SupplierSearchRequest(query="сталь", idempotency_key="search-fixture-web")
    )
    assert web.calls == 1
    assert result.web_fallback_used is True
    assert "web" in result.sources_used


@pytest.mark.asyncio
async def test_browser_adapter_maps_provider_items() -> None:
    class _Provider:
        async def search(self, query: str, limit: int) -> dict:
            assert "кабель" in query
            assert limit == 5
            return {
                "status": "available",
                "items": [
                    {
                        "title": "Кабель-Поставка",
                        "url": "https://example.com/cable",
                        "snippet": "г. Москва, от 1 250 руб. за метр",
                    },
                ],
            }

        async def fetch(self, url: str) -> dict:
            raise AssertionError(f"unexpected fetch: {url}")

    rows = await BrowserSupplierSearchAdapter(provider=_Provider()).search(
        "кабель", None, 5
    )
    assert len(rows) == 1
    assert rows[0].source == "web"
    assert rows[0].name == "Кабель-Поставка"
    assert rows[0].contacts.get("website") == "https://example.com/cable"
    assert rows[0].url == "https://example.com/cable"
    assert rows[0].city == "Москва"
    assert rows[0].approx_cost == Decimal("1250")


@pytest.mark.asyncio
async def test_search_runs_per_nomenclature_not_mega_query() -> None:
    web = _Adapter([Supplier(supplier_id="web-1", name="Web", source="web")])
    service = HybridSupplierSearchService(
        onec=_Adapter(fail=True),
        internal=_Adapter(),
        web=web,
    )
    result = await service.search(
        SupplierSearchRequest(
            query="Фильтр воздушный, Болт М8",
            allow_web_fallback=True,
            idempotency_key="per-nom-1",
        )
    )
    assert [row.query for row in result.nomenclature_results] == [
        "Фильтр воздушный",
        "Болт М8",
    ]
    assert web.calls == 2
    assert "Фильтр воздушный" in web.queries[0]
    assert "Болт М8" in web.queries[1]


@pytest.mark.asyncio
async def test_skips_nomenclature_with_at_least_three_existing_suppliers() -> None:
    web = _Adapter([Supplier(supplier_id="web-1", name="Web", source="web")])
    existing = [
        Supplier(supplier_id=f"ex-{index}", name=f"Existing {index}")
        for index in range(3)
    ]
    service = HybridSupplierSearchService(
        onec=_Adapter(fail=True),
        internal=_Adapter(),
        web=web,
    )
    result = await service.search(
        SupplierSearchRequest(
            nomenclatures=[
                NomenclatureSearchItem(
                    nomenclature_id="bolt",
                    nomenclature_name="Болт М8",
                    query="Болт М8",
                    existing_suppliers=existing,
                ),
                NomenclatureSearchItem(
                    nomenclature_id="filter",
                    nomenclature_name="Фильтр",
                    query="Фильтр",
                ),
            ],
            allow_web_fallback=True,
            idempotency_key="skip-3",
        )
    )
    by_id = {
        row.nomenclature_id: row for row in result.nomenclature_results
    }
    assert by_id["bolt"].sources_used == ["existing"]
    assert len(by_id["bolt"].suppliers) == 3
    assert web.calls == 1
    assert web.queries == ["Фильтр"]


@pytest.mark.asyncio
async def test_manual_force_web_runs_even_with_three_bank_seeds() -> None:
    """«Найти поставщиков»: bank seeds ≥3 must not block Edge/Bing web search."""
    web = _Adapter(
        [
            Supplier(
                supplier_id="web-live",
                name="Live Web Supplier",
                source="web",
                url="https://example.com/live",
            )
        ]
    )
    bank_seeds = [
        Supplier(
            supplier_id=f"ТехноТрейд-{index}",
            name=f"ООО ТехноТрейд-{index}",
            source="internal",
            evidence=[f"bank:ТехноТрейд-{index}"],
        )
        for index in range(3)
    ]
    service = HybridSupplierSearchService(
        onec=_Adapter(fail=True),
        internal=_Adapter(),
        web=web,
        internal_threshold=1,
    )
    result = await service.search(
        SupplierSearchRequest(
            nomenclatures=[
                NomenclatureSearchItem(
                    nomenclature_id="bolt",
                    nomenclature_name="Болт М8",
                    query="Болт М8",
                    existing_suppliers=bank_seeds,
                )
            ],
            allow_web_fallback=True,
            force_web=True,
            mode="manual_web",
            idempotency_key="force-web-bank",
        )
    )
    assert web.calls == 1
    row = result.nomenclature_results[0]
    assert "web" in row.sources_used
    assert row.web_fallback_used is True
    assert any(item.supplier_id == "web-live" and item.url for item in row.suppliers)
    # Manual force_web returns web-only cards (bank seeds stay in bank/top elsewhere).
    assert all(item.source == "web" for item in row.suppliers)
    assert not any(item.supplier_id.startswith("ТехноТрейд-") for item in row.suppliers)


@pytest.mark.asyncio
async def test_force_web_tracks_existing_links_before_serp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """≥3 prior URLs: enrich/track them and skip cold SERP."""
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "false")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT_SELECT_URLS", "false")
    monkeypatch.setenv("PROCUREMENT_WEB_ENRICH_ON_MANUAL_SEARCH", "auto")

    web = _Adapter(
        [
            Supplier(
                supplier_id="web-should-not-run",
                name="Should not SERP",
                source="web",
                url="https://example.com/new",
            )
        ]
    )
    fetched: list[str] = []

    class _Fetch:
        async def fetch(self, url: str) -> dict:
            fetched.append(url)
            return {
                "status": "available",
                "html": (
                    "<html><body><h1>Товар</h1>"
                    "<div>г. Пермь</div><div>1 500 ₽</div></body></html>"
                ),
            }

    prior = [
        Supplier(
            supplier_id=f"web-prior-{index}",
            name=f"Prior {index}",
            source="web",
            url=f"https://shop.example/prior-{index}",
        )
        for index in range(3)
    ]
    service = HybridSupplierSearchService(
        onec=_Adapter(fail=True),
        internal=_Adapter(),
        web=web,
        page_fetch_provider=_Fetch(),
    )
    result = await service.search(
        SupplierSearchRequest(
            nomenclatures=[
                NomenclatureSearchItem(
                    nomenclature_id="belt",
                    nomenclature_name="Ремень",
                    query="Ремень",
                    existing_suppliers=prior,
                )
            ],
            allow_web_fallback=True,
            force_web=True,
            mode="manual_web",
            idempotency_key="track-existing-enough",
        )
    )
    assert web.calls == 0
    assert fetched
    assert all("prior-" in url for url in fetched)
    row = result.nomenclature_results[0]
    assert "existing" in row.sources_used
    assert row.web_fallback_used is False
    assert len(row.suppliers) == 3
    assert any(item.city == "Пермь" for item in row.suppliers)
    assert any(item.approx_cost == Decimal("1500") for item in row.suppliers)


@pytest.mark.asyncio
async def test_force_web_tracks_few_links_then_falls_back_to_serp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1 prior URL: track it first, then SERP to fill up to threshold."""
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "false")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT_SELECT_URLS", "false")
    monkeypatch.setenv("PROCUREMENT_WEB_ENRICH_ON_MANUAL_SEARCH", "auto")

    web = _Adapter(
        [
            Supplier(
                supplier_id="web-serp",
                name="SERP Shop",
                source="web",
                url="https://shop.example/serp",
            )
        ]
    )
    fetched: list[str] = []

    class _Fetch:
        async def fetch(self, url: str) -> dict:
            fetched.append(url)
            return {
                "status": "available",
                "html": (
                    "<html><body><h1>Товар</h1>"
                    "<div>г. Казань</div><div>900 ₽</div></body></html>"
                ),
            }

    prior = [
        Supplier(
            supplier_id="web-prior-only",
            name="Prior Only",
            source="web",
            url="https://shop.example/prior-only",
        )
    ]
    service = HybridSupplierSearchService(
        onec=_Adapter(fail=True),
        internal=_Adapter(),
        web=web,
        page_fetch_provider=_Fetch(),
    )
    result = await service.search(
        SupplierSearchRequest(
            nomenclatures=[
                NomenclatureSearchItem(
                    nomenclature_id="belt",
                    nomenclature_name="Ремень",
                    query="Ремень",
                    existing_suppliers=prior,
                )
            ],
            allow_web_fallback=True,
            force_web=True,
            mode="manual_web",
            idempotency_key="track-existing-then-serp",
        )
    )
    assert web.calls == 1
    assert any("prior-only" in url for url in fetched)
    assert any("serp" in url for url in fetched)
    row = result.nomenclature_results[0]
    assert "existing" in row.sources_used
    assert "web" in row.sources_used
    assert row.web_fallback_used is True
    ids = {item.supplier_id for item in row.suppliers}
    assert "web-prior-only" in ids
    assert "web-serp" in ids


@pytest.mark.asyncio
async def test_force_web_enriches_from_product_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Agent path with Qwen disabled → regex extract still fills SERP cards.
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "false")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT_SELECT_URLS", "false")

    class _Web:
        async def search(self, query: str, category: str | None, limit: int) -> list[Supplier]:
            _ = (query, category, limit)
            return [
                Supplier(
                    supplier_id="web-enrich",
                    name="https://shop.example/belt",
                    source="web",
                    url="https://shop.example/belt",
                )
            ]

    class _Fetch:
        async def fetch(self, url: str) -> dict:
            assert "shop.example" in url
            return {
                "status": "available",
                "html": (
                    "<html><body><h1>Ремень А-1250</h1>"
                    "<div>г. Казань</div><div>2 340 ₽</div></body></html>"
                ),
            }

    service = HybridSupplierSearchService(
        onec=_Adapter(fail=True),
        internal=_Adapter(),
        web=_Web(),
        page_fetch_provider=_Fetch(),
    )
    result = await service.search(
        SupplierSearchRequest(
            query="Ремень клиновой А-1250",
            allow_web_fallback=True,
            force_web=True,
            mode="manual_web",
            idempotency_key="enrich-web",
        )
    )
    row = result.nomenclature_results[0]
    assert len(row.suppliers) == 1
    supplier = row.suppliers[0]
    assert supplier.source == "web"
    assert supplier.city == "Казань"
    assert supplier.approx_cost == Decimal("2340")
    # Shop/host brand preferred over product title on the card name.
    assert supplier.name
    assert "qwen_agent:visited" in supplier.evidence
    assert any(
        str(ev).startswith("page_title:") and "Ремень" in str(ev)
        for ev in supplier.evidence
    )
    assert result.diagnostics.get("qwen_browse_agent", {}).get("qwen_agent") is True


@pytest.mark.asyncio
async def test_multi_item_force_web_runs_qwen_agent_not_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto enrich mode must not skip browse agent on multi-nomenclature force_web."""
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT_SELECT_URLS", "false")
    monkeypatch.setenv("PROCUREMENT_WEB_ENRICH_ON_MANUAL_SEARCH", "auto")
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://127.0.0.1:9/v1")

    fetched: list[str] = []

    class _Web:
        async def search(self, query: str, category: str | None, limit: int) -> list[Supplier]:
            _ = (category, limit)
            slug = "belt" if "Ремень" in query else "bolt"
            return [
                Supplier(
                    supplier_id=f"web-{slug}",
                    name=f"Shop {slug}",
                    source="web",
                    url=f"https://shop.example/{slug}",
                )
            ]

    class _Fetch:
        async def fetch(self, url: str) -> dict:
            fetched.append(url)
            return {
                "status": "available",
                "html": (
                    "<html><body><h1>Товар</h1>"
                    "<div>г. Казань</div><div>1 100 ₽</div></body></html>"
                ),
            }

    async def _chat(messages, model=None, timeout=None, **kwargs):
        _ = (messages, model, timeout, kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"title":"Товар Qwen","unit_price":1100,"city":"Казань",'
                            '"lead_time_days":2,"delivery_hint":"2 дня",'
                            '"product_match_confidence":0.7}'
                        )
                    }
                }
            ]
        }

    # Patch extract path chat via browse agent → inject chat_fn through monkeypatch
    # of run_qwen_browse_agent's select + extract by patching web_qwen helpers.
    from app.agents.procurement_manager_agent import web_page_enrichment as enrich_mod

    original = enrich_mod.run_qwen_browse_agent

    async def _agent_with_chat(suppliers, fetch_provider, **kwargs):
        kwargs = {**kwargs, "chat_fn": _chat}
        return await original(suppliers, fetch_provider, **kwargs)

    monkeypatch.setattr(enrich_mod, "run_qwen_browse_agent", _agent_with_chat)
    # Hybrid imports run_qwen_browse_agent at module level — patch there too.
    import app.agents.procurement_manager_agent.suppliers as suppliers_mod

    monkeypatch.setattr(suppliers_mod, "run_qwen_browse_agent", _agent_with_chat)

    service = HybridSupplierSearchService(
        onec=_Adapter(fail=True),
        internal=_Adapter(),
        web=_Web(),
        page_fetch_provider=_Fetch(),
    )
    result = await service.search(
        SupplierSearchRequest(
            nomenclatures=[
                NomenclatureSearchItem(
                    nomenclature_id="n1",
                    nomenclature_name="Ремень",
                    query="Ремень клиновой",
                ),
                NomenclatureSearchItem(
                    nomenclature_id="n2",
                    nomenclature_name="Болт",
                    query="Болт М8",
                ),
            ],
            allow_web_fallback=True,
            force_web=True,
            mode="manual_web",
            idempotency_key="multi-agent",
        )
    )
    assert len(result.nomenclature_results) == 2
    assert len(fetched) >= 2
    for row in result.nomenclature_results:
        assert row.suppliers
        assert any("qwen_agent:visited" in item.evidence for item in row.suppliers)
    agent_diag = result.diagnostics.get("qwen_browse_agent") or {}
    assert agent_diag.get("qwen_agent") is True
    assert agent_diag.get("nomenclature_runs", 0) >= 2


@pytest.mark.asyncio
async def test_web_search_limit_capped_per_nomenclature() -> None:
    class _CountingWeb:
        def __init__(self) -> None:
            self.limits: list[int] = []

        async def search(self, query: str, category: str | None, limit: int) -> list[Supplier]:
            _ = (query, category)
            self.limits.append(limit)
            return [
                Supplier(
                    supplier_id=f"web-{index}",
                    name=f"Web {index}",
                    source="web",
                    url=f"https://example.com/{index}",
                )
                for index in range(limit)
            ]

    web = _CountingWeb()
    service = HybridSupplierSearchService(
        onec=_Adapter(fail=True),
        internal=_Adapter(),
        web=web,
        internal_threshold=1,
    )
    result = await service.search(
        SupplierSearchRequest(
            query="Лист стальной",
            limit=20,
            allow_web_fallback=True,
            idempotency_key="web-cap",
        )
    )
    assert web.limits == [WEB_LIMIT_PER_NOMENCLATURE]
    assert len(result.nomenclature_results) == 1
    assert len(result.nomenclature_results[0].suppliers) == WEB_LIMIT_PER_NOMENCLATURE


@pytest.mark.asyncio
async def test_browser_adapter_hard_caps_provider_limit() -> None:
    class _Provider:
        async def search(self, query: str, limit: int) -> dict:
            assert limit == WEB_LIMIT_PER_NOMENCLATURE
            return {
                "status": "available",
                "items": [
                    {"title": f"Item {index}", "url": f"https://example.com/{index}"}
                    for index in range(limit)
                ],
            }

        async def fetch(self, url: str) -> dict:
            raise AssertionError(f"unexpected fetch: {url}")

    rows = await BrowserSupplierSearchAdapter(provider=_Provider()).search(
        "швеллер", None, 50
    )
    assert len(rows) == WEB_LIMIT_PER_NOMENCLATURE


@pytest.mark.asyncio
async def test_force_web_agent_budget_returns_serp_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slow Qwen browse must not discard SERP hits (outer timeout used to return empty)."""
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT_SELECT_URLS", "false")
    monkeypatch.setenv("PROCUREMENT_WEB_ENRICH_ON_MANUAL_SEARCH", "auto")

    class _Web:
        async def search(self, query: str, category: str | None, limit: int) -> list[Supplier]:
            _ = (query, category, limit)
            return [
                Supplier(
                    supplier_id="web-serp",
                    name="SERP Shop",
                    source="web",
                    url="https://shop.example/belt",
                )
            ]

    class _SlowFetch:
        async def fetch(self, url: str) -> dict:
            _ = url
            await asyncio.sleep(2.0)
            return {"status": "available", "html": "<html><body>too late</body></html>"}

    import app.agents.procurement_manager_agent.suppliers as suppliers_mod

    async def _slow_agent(suppliers, fetch_provider, **kwargs):
        _ = (fetch_provider, kwargs)
        await asyncio.sleep(2.0)
        return list(suppliers), {"qwen_agent": True, "status": "completed"}

    monkeypatch.setattr(suppliers_mod, "run_qwen_browse_agent", _slow_agent)
    monkeypatch.setattr(suppliers_mod, "qwen_agent_budget_seconds", lambda: 0.05)

    service = HybridSupplierSearchService(
        onec=_Adapter(fail=True),
        internal=_Adapter(),
        web=_Web(),
        page_fetch_provider=_SlowFetch(),
    )
    result = await service.search(
        SupplierSearchRequest(
            query="Ремень",
            allow_web_fallback=True,
            force_web=True,
            mode="manual_web",
            idempotency_key="budget-serp",
        )
    )
    assert len(result.suppliers) == 1
    assert result.suppliers[0].supplier_id == "web-serp"
    assert result.suppliers[0].url == "https://shop.example/belt"
    diag = (result.diagnostics or {}).get("qwen_browse_agent") or {}
    assert diag.get("status") == "budget_timeout"
