"""Unit tests for Qwen web enrichment (mocked LLM; no live gateway required)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.agents.procurement_manager_agent.schemas import Supplier
from app.agents.procurement_manager_agent.web_page_enrichment import (
    apply_page_enrichment,
    enrich_web_suppliers,
    parse_product_page_with_qwen,
    run_qwen_browse_agent,
)
from app.agents.procurement_manager_agent.web_qwen import (
    normalize_qwen_page_fields,
    parse_json_object,
    refine_search_query_with_qwen,
    select_urls_to_visit_with_qwen,
    strip_think_blocks,
)


PAGE_HTML = """
<html><body>
  <h1>Ремень клиновой А-1250</h1>
  <div>Цена: 2 340 ₽</div>
  <div>г. Казань</div>
  <p>Доставка 5 дней</p>
</body></html>
"""


def test_strip_think_and_parse_json_object() -> None:
    raw = (
        "<think>reasoning...</think>\n"
        '```json\n{"title": "Ремень", "unit_price": 2340, '
        '"city": "Казань", "product_match_confidence": 0.9}\n```'
    )
    assert "reasoning" not in strip_think_blocks(raw)
    data = parse_json_object(raw)
    assert data["title"] == "Ремень"
    assert data["unit_price"] == 2340
    assert data["city"] == "Казань"


def test_normalize_qwen_page_fields() -> None:
    parsed = normalize_qwen_page_fields(
        {
            "title": "Ремень А-1250",
            "unit_price": "2 340,50",
            "city": "Казань",
            "lead_time_days": 5,
            "delivery_hint": None,
            "product_match_confidence": 0.85,
        },
        product_query="Ремень клиновой",
    )
    assert parsed is not None
    assert parsed["unit_price"] == Decimal("2340.50")
    assert parsed["city"] == "Казань"
    assert parsed["lead_time_days"] == 5
    assert "5" in (parsed["delivery_hint"] or "")
    assert parsed["enrichment_source"] == "qwen"


def test_normalize_rejects_empty_payload() -> None:
    assert normalize_qwen_page_fields({}) is None
    assert normalize_qwen_page_fields({"title": None, "unit_price": None}) is None


@pytest.mark.asyncio
async def test_parse_product_page_with_qwen_uses_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "true")
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://127.0.0.1:9/v1")

    async def _chat(messages, model=None, timeout=None, **kwargs):
        _ = (messages, model, timeout, kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '<think>x</think>{"title":"Ремень Qwen","unit_price":1999,'
                            '"city":"Уфа","lead_time_days":3,'
                            '"delivery_hint":"со склада","product_match_confidence":0.92}'
                        )
                    }
                }
            ]
        }

    parsed = await parse_product_page_with_qwen(
        PAGE_HTML,
        product_query="Ремень клиновой А-1250",
        page_url="https://shop.example/belt",
        chat_fn=_chat,
    )
    assert parsed["enrichment_source"] == "qwen"
    assert parsed["unit_price"] == Decimal("1999")
    assert parsed["city"] == "Уфа"
    assert parsed["title"] == "Ремень Qwen"
    assert parsed["lead_time_days"] == 3


@pytest.mark.asyncio
async def test_parse_product_page_falls_back_to_regex_on_llm_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "true")
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://127.0.0.1:9/v1")

    async def _boom(messages, model=None, timeout=None, **kwargs):
        _ = (messages, model, timeout, kwargs)
        raise TimeoutError("llm down")

    parsed = await parse_product_page_with_qwen(
        PAGE_HTML,
        product_query="Ремень",
        chat_fn=_boom,
    )
    assert parsed["enrichment_source"] == "regex"
    assert parsed["unit_price"] == Decimal("2340")
    assert parsed["city"] == "Казань"


@pytest.mark.asyncio
async def test_enrich_web_suppliers_with_mocked_qwen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "true")

    class _Provider:
        async def fetch(self, url: str) -> dict:
            _ = url
            return {"status": "available", "html": PAGE_HTML}

    async def _chat(messages, model=None, timeout=None, **kwargs):
        _ = (messages, model, timeout, kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"title":"Ремень А-1250","shop_name":"BeltShop",'
                            '"unit_price":2500,"city":"Пермь",'
                            '"lead_time_days":7,"delivery_hint":"7 дней",'
                            '"product_match_confidence":0.8}'
                        )
                    }
                }
            ]
        }

    supplier = Supplier(
        supplier_id="web-1",
        name="https://shop.example/belt",
        source="web",
        url="https://shop.example/belt",
    )
    enriched = await enrich_web_suppliers(
        [supplier],
        _Provider(),
        concurrency=1,
        timeout_seconds=5,
        max_pages=1,
        product_query="Ремень клиновой",
        chat_fn=_chat,
    )
    assert enriched[0].approx_cost == Decimal("2500")
    assert enriched[0].city == "Пермь"
    assert enriched[0].name == "BeltShop"
    assert any(item == "enrichment:qwen" for item in enriched[0].evidence)
    assert any(item.startswith("match_confidence:") for item in enriched[0].evidence)


@pytest.mark.asyncio
async def test_enrich_disabled_qwen_uses_regex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "false")

    class _Provider:
        async def fetch(self, url: str) -> dict:
            _ = url
            return {"status": "available", "html": PAGE_HTML}

    async def _should_not_run(*args, **kwargs):
        raise AssertionError("Qwen chat must not be called when disabled")

    supplier = Supplier(
        supplier_id="web-1",
        name="Shop",
        source="web",
        url="https://shop.example/belt",
    )
    enriched = await enrich_web_suppliers(
        [supplier],
        _Provider(),
        concurrency=1,
        timeout_seconds=5,
        max_pages=1,
        chat_fn=_should_not_run,
    )
    assert enriched[0].approx_cost == Decimal("2340")
    assert enriched[0].city == "Казань"


@pytest.mark.asyncio
async def test_refine_search_query_with_qwen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_REFINE_QUERY", "true")
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://127.0.0.1:9/v1")

    async def _chat(messages, model=None, timeout=None, **kwargs):
        _ = (messages, model, timeout, kwargs)
        return {
            "choices": [
                {"message": {"content": "<think>..</think>купить ремень клиновой А-1250"}}
            ]
        }

    refined = await refine_search_query_with_qwen(
        "Нужен ремень клиновой А-1250 для компрессора, желательно со склада в РФ",
        chat_fn=_chat,
    )
    assert refined == "купить ремень клиновой А-1250"


@pytest.mark.asyncio
async def test_refine_query_disabled_returns_original(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_REFINE_QUERY", "false")
    original = "Ремень клиновой А-1250"
    assert await refine_search_query_with_qwen(original) == original


def test_apply_page_enrichment_records_qwen_evidence() -> None:
    supplier = Supplier(
        supplier_id="web-1",
        name="https://x",
        source="web",
        url="https://x",
    )
    updated = apply_page_enrichment(
        supplier,
        {
            "unit_price": Decimal("100"),
            "city": "Тула",
            "title": "Товар",
            "delivery_hint": "2 дня",
            "lead_time_days": 2,
            "product_match_confidence": Decimal("0.7"),
            "enrichment_source": "qwen",
        },
    )
    assert updated.city == "Тула"
    assert "enrichment:qwen" in updated.evidence
    assert "lead_time_days:2" in updated.evidence


@pytest.mark.asyncio
async def test_select_urls_to_visit_with_qwen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT_SELECT_URLS", "true")
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://127.0.0.1:9/v1")

    async def _chat(messages, model=None, timeout=None, **kwargs):
        _ = (messages, model, timeout, kwargs)
        return {"choices": [{"message": {"content": '{"visit_indexes": [2, 0]}'}}]}

    hits = [
        {"title": "Новость", "url": "https://news.example/1", "snippet": "новости"},
        {"title": "Форум", "url": "https://forum.example/1", "snippet": "обсуждение"},
        {"title": "Купить ремень", "url": "https://shop.example/belt", "snippet": "2340 ₽"},
    ]
    indexes = await select_urls_to_visit_with_qwen(
        hits,
        product_query="Ремень",
        max_visit=2,
        chat_fn=_chat,
    )
    assert indexes == [2, 0]


@pytest.mark.asyncio
async def test_select_urls_falls_back_when_gateway_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT_SELECT_URLS", "true")
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("VISION_LM_STUDIO_BASE_URL", raising=False)

    hits = [
        {"title": "A", "url": "https://a.example", "snippet": ""},
        {"title": "B", "url": "https://b.example", "snippet": ""},
        {"title": "C", "url": "https://c.example", "snippet": ""},
    ]
    indexes = await select_urls_to_visit_with_qwen(hits, max_visit=2)
    assert indexes == [0, 1]


@pytest.mark.asyncio
async def test_run_qwen_browse_agent_visits_and_enriches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT_SELECT_URLS", "true")
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://127.0.0.1:9/v1")

    class _Provider:
        def __init__(self) -> None:
            self.fetched: list[str] = []

        async def fetch(self, url: str) -> dict:
            self.fetched.append(url)
            return {"status": "available", "html": PAGE_HTML}

    async def _chat(messages, model=None, timeout=None, **kwargs):
        _ = (model, timeout, kwargs)
        content = messages[-1]["content"] if messages else ""
        if "visit_indexes" in (messages[0].get("content") or "") or "hits" in content:
            return {"choices": [{"message": {"content": '{"visit_indexes": [0]}'}}]}
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"title":"Ремень А-1250","unit_price":2500,"city":"Пермь",'
                            '"lead_time_days":7,"delivery_hint":"7 дней",'
                            '"product_match_confidence":0.8}'
                        )
                    }
                }
            ]
        }

    provider = _Provider()
    suppliers = [
        Supplier(
            supplier_id="web-1",
            name="Shop",
            source="web",
            url="https://shop.example/belt",
            evidence=["https://shop.example/belt", "цена от 2000"],
        ),
        Supplier(
            supplier_id="web-2",
            name="Other",
            source="web",
            url="https://other.example/x",
        ),
    ]
    enriched, diagnostics = await run_qwen_browse_agent(
        suppliers,
        provider,
        product_query="Ремень клиновой",
        max_pages=1,
        concurrency=1,
        timeout_seconds=5,
        chat_fn=_chat,
    )
    assert diagnostics["qwen_agent"] is True
    assert diagnostics["status"] == "completed"
    assert diagnostics["visited"] == 1
    assert provider.fetched == ["https://shop.example/belt"]
    assert enriched[0].city == "Пермь"
    assert enriched[0].approx_cost == Decimal("2500")
    assert "qwen_agent:visited" in enriched[0].evidence
    assert "enrichment:qwen" in enriched[0].evidence
    # Second SERP card kept untouched (not visited).
    assert enriched[1].supplier_id == "web-2"
    assert "qwen_agent:visited" not in enriched[1].evidence


@pytest.mark.asyncio
async def test_run_qwen_browse_agent_keeps_serp_when_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT", "true")
    monkeypatch.setenv("PROCUREMENT_WEB_QWEN_AGENT_SELECT_URLS", "false")

    class _Boom:
        async def fetch(self, url: str) -> dict:
            _ = url
            raise TimeoutError("browser down")

    suppliers = [
        Supplier(
            supplier_id="web-1",
            name="Shop",
            source="web",
            url="https://shop.example/belt",
        )
    ]
    enriched, diagnostics = await run_qwen_browse_agent(
        suppliers,
        _Boom(),
        product_query="Ремень",
        max_pages=2,
        concurrency=1,
        timeout_seconds=1,
    )
    assert len(enriched) == 1
    assert enriched[0].url == "https://shop.example/belt"
    assert diagnostics["visited"] == 0
    assert diagnostics["status"] == "no_pages_fetched"
