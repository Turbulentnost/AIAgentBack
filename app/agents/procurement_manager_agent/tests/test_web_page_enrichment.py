"""Unit tests for web product-page price/city parsers and enrichment."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.agents.procurement_manager_agent.schemas import Supplier
from app.agents.procurement_manager_agent.web_page_enrichment import (
    apply_page_enrichment,
    enrich_web_suppliers,
    extract_city,
    extract_price,
    extract_rating,
    extract_title,
    parse_product_page,
)


@pytest.fixture(autouse=True)
def _disable_qwen_for_regex_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests assert regex parsing; Qwen path is covered in test_web_qwen.py."""
    monkeypatch.setenv("PROCUREMENT_WEB_USE_QWEN", "false")


SAMPLE_HTML = """
<html>
<head>
  <title>Ремень клиновой А-1250 — купить в Москве</title>
  <meta itemprop="price" content="1450.50" />
  <meta property="og:locality" content="Москва" />
</head>
<body>
  <h1>Ремень клиновой А-1250</h1>
  <div class="price">Цена: 1 450,50 ₽</div>
  <div class="city">г. Москва, склад на Юге</div>
  <p>Доставка по России от 2 дней</p>
  <span itemprop="ratingValue">4.6</span>
</body>
</html>
"""


def test_extract_price_from_rub_text() -> None:
    assert extract_price("от 1 250 руб. за метр") == Decimal("1250")
    assert extract_price("Стоимость 890₽") == Decimal("890")


def test_extract_price_from_meta_and_data_attrs() -> None:
    html = '<div data-price="1999.00"></div><span itemprop="price" content="2100">'
    assert extract_price(html) == Decimal("1999.00")


def test_extract_city_patterns() -> None:
    assert extract_city("г. Санкт-Петербург, РФ") == "Санкт-Петербург"
    assert extract_city(SAMPLE_HTML) in {"Москва", "г. Москва"} or "Москва" in (
        extract_city(SAMPLE_HTML) or ""
    )


def test_extract_title_prefers_h1() -> None:
    assert extract_title(SAMPLE_HTML) == "Ремень клиновой А-1250"


def test_extract_rating_normalizes_five_star_scale() -> None:
    assert extract_rating(SAMPLE_HTML) == Decimal("92.00")


def test_extract_rating_does_not_invent_default() -> None:
    assert extract_rating("<html><body>Купить ремень</body></html>") is None


def test_parse_product_page_bundle() -> None:
    parsed = parse_product_page(SAMPLE_HTML)
    assert parsed["unit_price"] == Decimal("1450.50") or parsed["unit_price"] == Decimal(
        "1450.5"
    )
    assert parsed["city"]
    assert "Москва" in str(parsed["city"])
    assert parsed["title"] == "Ремень клиновой А-1250"
    assert parsed["delivery_hint"]


def test_apply_page_enrichment_fills_missing_fields_only() -> None:
    supplier = Supplier(
        supplier_id="web-1",
        name="https://shop.example/belt",
        source="web",
        url="https://shop.example/belt",
    )
    updated = apply_page_enrichment(
        supplier,
        {
            "unit_price": Decimal("1450.50"),
            "city": "Москва",
            "rating": Decimal("92.00"),
            "title": "Ремень клиновой А-1250",
            "delivery_hint": "Доставка по России от 2 дней",
        },
    )
    assert updated.unit_price == Decimal("1450.50")
    assert updated.approx_cost == Decimal("1450.50")
    assert updated.city == "Москва"
    assert updated.rating == Decimal("92.00")
    assert updated.name == "Ремень клиновой А-1250"
    assert any(item.startswith("delivery:") for item in updated.evidence)


@pytest.mark.asyncio
async def test_enrich_web_suppliers_uses_fetch_and_skips_failures() -> None:
    class _Provider:
        async def fetch(self, url: str) -> dict:
            if "fail" in url:
                return {"status": "unavailable", "message": "timeout"}
            return {"status": "available", "html": SAMPLE_HTML, "content": "1 450 ₽ Москва"}

    suppliers = [
        Supplier(
            supplier_id="web-ok",
            name="Shop",
            source="web",
            url="https://example.com/ok",
        ),
        Supplier(
            supplier_id="web-fail",
            name="Fail",
            source="web",
            url="https://example.com/fail",
        ),
        Supplier(
            supplier_id="internal-1",
            name="Bank",
            source="internal",
        ),
    ]
    enriched = await enrich_web_suppliers(
        suppliers,
        _Provider(),
        concurrency=2,
        timeout_seconds=5,
        max_pages=5,
    )
    by_id = {item.supplier_id: item for item in enriched}
    assert by_id["web-ok"].city
    assert by_id["web-ok"].approx_cost is not None
    assert by_id["web-fail"].city is None
    assert by_id["internal-1"].source == "internal"


@pytest.mark.asyncio
async def test_enrich_web_suppliers_timeout_keeps_url() -> None:
    class _Slow:
        async def fetch(self, url: str) -> dict:
            import asyncio

            await asyncio.sleep(2)
            return {"status": "available", "html": SAMPLE_HTML}

    supplier = Supplier(
        supplier_id="web-slow",
        name="Slow",
        source="web",
        url="https://example.com/slow",
    )
    enriched = await enrich_web_suppliers(
        [supplier],
        _Slow(),
        concurrency=1,
        timeout_seconds=0.05,
        max_pages=1,
    )
    assert enriched[0].url == "https://example.com/slow"
    assert enriched[0].approx_cost is None
