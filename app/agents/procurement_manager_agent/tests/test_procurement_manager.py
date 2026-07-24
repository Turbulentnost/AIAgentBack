from __future__ import annotations

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
async def test_force_web_enriches_from_product_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Assert regex enrichment path; Qwen mocked coverage lives in test_web_qwen.py.
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "false")

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
    assert "Ремень" in supplier.name


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
