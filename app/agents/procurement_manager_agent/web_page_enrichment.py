"""Enrich web supplier cards by fetching product pages in an isolated browser."""

from __future__ import annotations

import asyncio
import html
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Protocol

from app.agents.procurement_manager_agent.schemas import Supplier
from app.agents.procurement_manager_agent.web_qwen import (
    extract_product_fields_with_qwen,
    merge_parsed_fields,
    qwen_timeout_seconds,
)

ChatFn = Callable[..., Awaitable[dict[str, Any]]]

# Cap parallel page fetches (each opens an isolated headless browser).
DEFAULT_ENRICH_CONCURRENCY = 3
DEFAULT_ENRICH_TIMEOUT_SECONDS = 18.0
DEFAULT_ENRICH_MAX_PER_BATCH = 5

_PRICE_PATTERNS = (
    re.compile(
        r"(?:от\s*)?"
        r"(\d{1,3}(?:[ \u00a0]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
        r"\s*(?:₽|руб\.?|RUB|р\.)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:цена|стоимость|price)[:\s]*"
        r"(\d{1,3}(?:[ \u00a0]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"""(?:itemprop=["']price["'][^>]*content=["']|content=["']|"?price"?\s*[:=]\s*["']?)"""
        r"""(\d+(?:[.,]\d{1,2})?)""",
        re.IGNORECASE,
    ),
    re.compile(
        r"""(?:data-price|data-product-price)=["'](\d+(?:[.,]\d{1,2})?)["']""",
        re.IGNORECASE,
    ),
)

_CITY_PATTERNS = (
    re.compile(
        r"(?:г\.?\s*|город\s+|г\.о\.\s*)"
        r"([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё\-]+(?:[\s\-][A-ZА-ЯЁ][A-Za-zА-Яа-яЁё\-]+)?)",
        re.UNICODE,
    ),
    re.compile(
        r"([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё\-]+)\s*,\s*(?:РФ|Россия|обл)",
        re.UNICODE,
    ),
    re.compile(
        r"(?:доставка|склад|самовывоз|офис)[^.\n]{0,40}?"
        r"(?:г\.?\s*|город\s+)?"
        r"([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё\-]+(?:[\s\-][A-ZА-ЯЁ][A-Za-zА-Яа-яЁё\-]+)?)",
        re.IGNORECASE | re.UNICODE,
    ),
    re.compile(
        r"""(?:og:locality|addressLocality|content=["'])"""
        r"""([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё\-\s]{1,40})""",
        re.IGNORECASE | re.UNICODE,
    ),
)

_RATING_PATTERNS = (
    re.compile(
        r"(?:рейтинг|rating|оценка)[:\s]*(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:/|из)?\s*(?:5|100)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"""itemprop=["']ratingValue["'][^>]*content=["'](\d{1,2}(?:[.,]\d{1,2})?)["']""",
        re.IGNORECASE,
    ),
    re.compile(
        r"""itemprop=["']ratingValue["'][^>]*>\s*(\d{1,2}(?:[.,]\d{1,2})?)\s*<""",
        re.IGNORECASE,
    ),
    re.compile(
        r"""aggregateRating[^>]{0,80}ratingValue["']?\s*[:=]\s*["']?"""
        r"""(\d{1,2}(?:[.,]\d{1,2})?)""",
        re.IGNORECASE,
    ),
)

_TITLE_RE = re.compile(
    r"<title[^>]*>(.*?)</title>",
    re.IGNORECASE | re.DOTALL,
)
_H1_RE = re.compile(
    r"<h1[^>]*>(.*?)</h1>",
    re.IGNORECASE | re.DOTALL,
)
_DELIVERY_RE = re.compile(
    r"((?:доставка|срок\s+поставки|отправк[аи])[^.\n]{0,80})",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_KNOWN_CITIES = frozenset(
    {
        "москва",
        "санкт-петербург",
        "петербург",
        "новосибирск",
        "екатеринбург",
        "казань",
        "нижний новгород",
        "челябинск",
        "самара",
        "омск",
        "ростов-на-дону",
        "уфа",
        "красноярск",
        "воронеж",
        "пермь",
        "волгоград",
        "краснодар",
        "саратов",
        "тюмень",
        "тольятти",
        "ижевск",
        "барнаул",
        "иркутск",
        "хабаровск",
        "ярославль",
        "владивосток",
        "махачкала",
        "томск",
        "оренбург",
        "кемерово",
        "рязань",
        "астрахань",
        "пенза",
        "липецк",
        "тула",
        "киров",
        "чебоксары",
        "калининград",
        "брянск",
        "курск",
        "иваново",
        "магнитогорск",
        "тверь",
        "ставрополь",
        "нижний тагил",
        "белгород",
        "архангельск",
        "владимир",
        "сочи",
        "смоленск",
        "калуга",
        "чита",
        "саратов",
        "ульяновск",
    }
)


class PageFetchProvider(Protocol):
    async def fetch(self, url: str) -> dict[str, Any]: ...


def _strip_tags(value: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value or ""))).strip()


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def extract_price(html_or_text: str) -> Decimal | None:
    """Parse approximate unit price from page HTML or cleaned text."""
    if not html_or_text:
        return None
    candidates: list[Decimal] = []
    for pattern in _PRICE_PATTERNS:
        for match in pattern.finditer(html_or_text):
            price = _to_decimal(match.group(1))
            if price is None:
                continue
            # Ignore noise like years / tiny tokens; keep plausible RUB product prices.
            if Decimal("1") <= price <= Decimal("50000000"):
                candidates.append(price)
    if not candidates:
        return None
    # Prefer the first plausible price (usually the product offer).
    return candidates[0]


def extract_city(html_or_text: str) -> str | None:
    """Parse city/region if present on the page."""
    if not html_or_text:
        return None
    for pattern in _CITY_PATTERNS:
        match = pattern.search(html_or_text)
        if not match:
            continue
        city = _strip_tags(match.group(1)).strip(" ,.;:\"'")
        if not city or len(city) < 2 or len(city) > 60:
            continue
        # Prefer known city names when the capture is noisy.
        lowered = city.casefold()
        if lowered in _KNOWN_CITIES or any(
            known in lowered for known in _KNOWN_CITIES if len(known) >= 5
        ):
            return city.title() if city.islower() else city
        if city[0].isupper() or city[0].isupper():
            return city
    text = _strip_tags(html_or_text).casefold()
    for known in sorted(_KNOWN_CITIES, key=len, reverse=True):
        if known in text:
            return known.title()
    return None


def extract_rating(html_or_text: str) -> Decimal | None:
    """Extract a real rating signal only; never invent a default score."""
    if not html_or_text:
        return None
    for pattern in _RATING_PATTERNS:
        match = pattern.search(html_or_text)
        if not match:
            continue
        raw = _to_decimal(match.group(1))
        if raw is None:
            continue
        # Normalize 0–5 scales to 0–100 when clearly a 5-star rating.
        if raw <= Decimal("5"):
            return (raw * Decimal("20")).quantize(Decimal("0.01"))
        if raw <= Decimal("100"):
            return raw.quantize(Decimal("0.01"))
    return None


def extract_title(html_or_text: str) -> str | None:
    if not html_or_text:
        return None
    for pattern in (_H1_RE, _TITLE_RE):
        match = pattern.search(html_or_text)
        if not match:
            continue
        title = _strip_tags(match.group(1))
        if title and 3 <= len(title) <= 200:
            return title
    return None


def extract_delivery_hint(html_or_text: str) -> str | None:
    if not html_or_text:
        return None
    match = _DELIVERY_RE.search(_strip_tags(html_or_text) if "<" in html_or_text else html_or_text)
    if not match:
        return None
    hint = _WS_RE.sub(" ", match.group(1)).strip(" .;")
    return hint[:160] if hint else None


def parse_product_page(html_or_text: str) -> dict[str, Any]:
    """Pure parser used by enrichment and unit tests."""
    return {
        "unit_price": extract_price(html_or_text),
        "city": extract_city(html_or_text),
        "rating": extract_rating(html_or_text),
        "title": extract_title(html_or_text),
        "delivery_hint": extract_delivery_hint(html_or_text),
    }


def apply_page_enrichment(supplier: Supplier, parsed: dict[str, Any]) -> Supplier:
    """Merge parsed page fields into a web supplier without inventing ratings."""
    updates: dict[str, Any] = {}
    price = parsed.get("unit_price")
    if isinstance(price, Decimal) and supplier.unit_price is None and supplier.approx_cost is None:
        updates["unit_price"] = price
        updates["approx_cost"] = price
    elif isinstance(price, Decimal) and supplier.approx_cost is None:
        updates["approx_cost"] = price
        if supplier.unit_price is None:
            updates["unit_price"] = price

    city = parsed.get("city")
    if isinstance(city, str) and city.strip() and not supplier.city:
        updates["city"] = city.strip()

    rating = parsed.get("rating")
    if isinstance(rating, Decimal) and supplier.rating is None:
        updates["rating"] = rating

    evidence = list(supplier.evidence)
    source = parsed.get("enrichment_source")
    if source == "qwen":
        evidence.append("enrichment:qwen")
    elif source:
        evidence.append(f"enrichment:{source}")

    confidence = parsed.get("product_match_confidence")
    if isinstance(confidence, Decimal):
        evidence.append(f"match_confidence:{confidence}")

    lead_days = parsed.get("lead_time_days")
    if isinstance(lead_days, int):
        evidence.append(f"lead_time_days:{lead_days}")

    title = parsed.get("title")
    if isinstance(title, str) and title.strip():
        # Confirm/cleanup display name when SERP title is a bare domain/snippet.
        name = supplier.name or ""
        if (
            not name
            or name.startswith("http")
            or len(name) < 8
            or name.casefold() in {"результат поиска", "web"}
        ):
            updates["name"] = title.strip()[:240]
        evidence.append(f"page_title:{title.strip()[:200]}")

    delivery = parsed.get("delivery_hint")
    if isinstance(delivery, str) and delivery.strip():
        evidence.append(f"delivery:{delivery.strip()[:160]}")

    if evidence != supplier.evidence:
        updates["evidence"] = list(dict.fromkeys(evidence))

    if not updates:
        return supplier
    return supplier.model_copy(update=updates)


async def parse_product_page_with_qwen(
    html_or_text: str,
    *,
    product_query: str | None = None,
    page_url: str | None = None,
    chat_fn: ChatFn | None = None,
) -> dict[str, Any]:
    """Qwen extraction with regex fallback so enrichment always has a parser path."""
    regex_parsed = parse_product_page(html_or_text)
    try:
        qwen_parsed = await asyncio.wait_for(
            extract_product_fields_with_qwen(
                html_or_text,
                product_query=product_query,
                page_url=page_url,
                chat_fn=chat_fn,
            ),
            timeout=qwen_timeout_seconds() + 2.0,
        )
    except Exception:
        qwen_parsed = None
    if not qwen_parsed:
        regex_parsed["enrichment_source"] = "regex"
        return regex_parsed
    return merge_parsed_fields(qwen_parsed, regex_parsed)


async def enrich_web_suppliers(
    suppliers: list[Supplier],
    fetch_provider: PageFetchProvider,
    *,
    concurrency: int | None = None,
    timeout_seconds: float | None = None,
    max_pages: int | None = None,
    product_query: str | None = None,
    chat_fn: ChatFn | None = None,
) -> list[Supplier]:
    """Fetch each web supplier URL and fill price/city/rating when possible.

    After the isolated headless browser returns HTML/text, Qwen extracts
    structured fields when enabled; regex enrichment is the fallback.
    Failures are skipped (URL kept). Concurrency is capped. Never hangs forever.
    """
    if not suppliers:
        return []
    concurrency = max(
        1,
        concurrency
        or int(os.environ.get("PROCUREMENT_WEB_ENRICH_CONCURRENCY", DEFAULT_ENRICH_CONCURRENCY)),
    )
    timeout_seconds = float(
        timeout_seconds
        if timeout_seconds is not None
        else os.environ.get(
            "PROCUREMENT_WEB_ENRICH_TIMEOUT_SECONDS",
            DEFAULT_ENRICH_TIMEOUT_SECONDS,
        )
    )
    max_pages = max(
        0,
        max_pages
        if max_pages is not None
        else int(
            os.environ.get("PROCUREMENT_WEB_ENRICH_MAX_PER_BATCH", DEFAULT_ENRICH_MAX_PER_BATCH)
        ),
    )
    semaphore = asyncio.Semaphore(concurrency)
    result = list(suppliers)

    async def _one(index: int, supplier: Supplier) -> None:
        url = supplier.url or supplier.contacts.get("website")
        if not url or supplier.source != "web":
            return
        if index >= max_pages:
            return
        async with semaphore:
            try:
                response = await asyncio.wait_for(
                    fetch_provider.fetch(url),
                    timeout=timeout_seconds,
                )
            except Exception:
                return
        if not isinstance(response, dict) or response.get("status") != "available":
            return
        document = str(response.get("html") or response.get("content") or "")
        if not document.strip():
            return
        parsed = await parse_product_page_with_qwen(
            document,
            product_query=product_query,
            page_url=url,
            chat_fn=chat_fn,
        )
        result[index] = apply_page_enrichment(supplier, parsed)

    await asyncio.gather(
        *[_one(index, supplier) for index, supplier in enumerate(suppliers)],
        return_exceptions=True,
    )
    return result


__all__ = [
    "DEFAULT_ENRICH_CONCURRENCY",
    "DEFAULT_ENRICH_TIMEOUT_SECONDS",
    "apply_page_enrichment",
    "enrich_web_suppliers",
    "extract_city",
    "extract_delivery_hint",
    "extract_price",
    "extract_rating",
    "extract_title",
    "parse_product_page",
    "parse_product_page_with_qwen",
]
