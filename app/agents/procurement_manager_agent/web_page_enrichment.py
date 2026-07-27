"""Enrich web supplier cards by fetching product pages in an isolated browser.

Also hosts the Qwen browse+extract agent used by manual force_web search:
SERP hits → (optional) Qwen picks URLs → fetch pages → Qwen/regex extract.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import urlparse

from app.agents.procurement_manager_agent.schemas import Supplier
from app.agents.procurement_manager_agent.search_progress import (
    emit_progress,
    progress_domain,
)
from app.agents.procurement_manager_agent.web_qwen import (
    clamp_agent_pages,
    extract_product_fields_with_qwen,
    merge_parsed_fields,
    qwen_agent_concurrency,
    qwen_agent_enabled,
    qwen_agent_max_pages,
    qwen_timeout_seconds,
    qwen_web_enabled,
    resolve_qwen_gateway_url,
    select_urls_to_visit_with_qwen,
)

ChatFn = Callable[..., Awaitable[dict[str, Any]]]

logger = logging.getLogger(__name__)

# Cap parallel page fetches (each opens an isolated headless browser).
DEFAULT_ENRICH_CONCURRENCY = 3
DEFAULT_ENRICH_TIMEOUT_SECONDS = 18.0
DEFAULT_ENRICH_MAX_PER_BATCH = 3
DEFAULT_AGENT_FETCH_TIMEOUT_SECONDS = 14.0

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
_SITE_NAME_RE = re.compile(
    r"""<meta[^>]+(?:property|name)=["'](?:og:site_name|application-name|apple-mobile-web-app-title)["'][^>]*content=["']([^"']+)["']"""
    r"""|<meta[^>]+content=["']([^"']+)["'][^>]*(?:property|name)=["'](?:og:site_name|application-name|apple-mobile-web-app-title)["']""",
    re.IGNORECASE,
)
_TITLE_BRAND_SPLIT_RE = re.compile(r"\s*[|–—•›»]\s*|\s+[-—]\s+")
_PRODUCTISH_RE = re.compile(
    r"(купить|цена|опт(?:ом)?|розниц|каталог|товар|микросхем|ремень|кабель|"
    r"поставк|шт\.?|мм\b|в\s+наличии|со\s+склад)",
    re.IGNORECASE,
)
_WEB_ID_RE = re.compile(r"^web-[0-9a-f-]{6,}$", re.IGNORECASE)
_PART_NUMBER_RE = re.compile(r"^[A-Z0-9][-A-Z0-9/.\s]{1,24}$")
_NAME_STOPWORDS = frozenset(
    {
        "товар",
        "купить",
        "каталог",
        "результат поиска",
        "web",
        "поставщик",
        "shop",
        "store",
    }
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


def hostname_from_url(url: str | None) -> str | None:
    """Registrable-ish host without www; None if URL is empty/invalid."""
    raw = (url or "").strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    except Exception:
        return None
    host = (parsed.hostname or "").strip(".").casefold()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _prettify_brand(token: str) -> str:
    text = _WS_RE.sub(" ", (token or "").strip()).strip(" .,;:\"'")
    if not text:
        return text
    # Keep mixed-case brands (ChipDip); capitalize plain lowercase tokens.
    if text.islower() and " " not in text and text.replace("-", "").isalnum():
        return text[:1].upper() + text[1:]
    if text.isupper() and len(text) > 3 and text.isalpha():
        return text.title()
    return text


def _looks_like_brand_token(text: str) -> bool:
    token = _WS_RE.sub(" ", (text or "").strip()).strip(" .,;:\"'")
    if not token or len(token) < 2 or len(token) > 40:
        return False
    if len(token.split()) > 4:
        return False
    if _PART_NUMBER_RE.fullmatch(token.replace(" ", "")):
        return False
    if _PRODUCTISH_RE.search(token) and len(token) > 18:
        return False
    # Reject long product-y phrases even without keywords.
    if len(token) > 28 and " " in token:
        return False
    return True


def brand_from_title(title: str | None) -> str | None:
    """Pull shop/brand segment from SERP/page titles like '… - chipdip'."""
    text = _WS_RE.sub(" ", (title or "").strip())
    if not text:
        return None
    parts = [part.strip() for part in _TITLE_BRAND_SPLIT_RE.split(text) if part and part.strip()]
    if len(parts) < 2:
        return None
    for candidate in reversed(parts):
        if _looks_like_brand_token(candidate):
            return _prettify_brand(candidate)
    if _looks_like_brand_token(parts[0]):
        return _prettify_brand(parts[0])
    return None


def is_weak_web_supplier_name(name: str | None, *, url: str | None = None) -> bool:
    """True when name is a query, product title, URL, or synthetic web id."""
    text = _WS_RE.sub(" ", (name or "").strip())
    if not text:
        return True
    if _WEB_ID_RE.fullmatch(text):
        return True
    if text.startswith("http://") or text.startswith("https://"):
        return True
    if text.casefold() in _NAME_STOPWORDS:
        return True
    host = hostname_from_url(url)
    if host and text.casefold() in {host, host.removeprefix("www.")}:
        return False
    if len(text) > 55:
        return True
    # Product/query phrases are not shop names (keep short company labels like «Кабель-Поставка»).
    if _PRODUCTISH_RE.search(text) and brand_from_title(text) is None:
        if (
            "|" in text
            or "—" in text
            or " - " in text
            or "–" in text
            or len(text) > 28
            or len(text.split()) >= 2
        ):
            return True
    # Long multi-word titles without a clear brand segment.
    if len(text.split()) >= 5 and brand_from_title(text) is None:
        return True
    return False


def derive_web_supplier_name(
    *,
    url: str | None,
    title: str | None = None,
    name: str | None = None,
    shop_name: str | None = None,
    site_name: str | None = None,
) -> str:
    """Human shop/distributor label for web cards (never raw query / web-uuid).

    Preference: enriched shop/company → site brand in title → hostname.
    """
    for candidate in (shop_name, site_name):
        cleaned = _prettify_brand(str(candidate)) if candidate else ""
        if cleaned and not is_weak_web_supplier_name(cleaned, url=url):
            return cleaned[:120]

    for candidate in (name, title):
        if not candidate:
            continue
        text = _WS_RE.sub(" ", str(candidate).strip())
        brand = brand_from_title(text)
        if brand:
            return brand[:120]
        if not is_weak_web_supplier_name(text, url=url):
            return text[:120]

    host = hostname_from_url(url)
    if host:
        return host[:120]

    for candidate in (name, title):
        text = _WS_RE.sub(" ", str(candidate or "").strip())
        if text and not _WEB_ID_RE.fullmatch(text):
            return text[:120]
    return "Поставщик"


def extract_site_name(html_or_text: str) -> str | None:
    """Shop/site brand from og:site_name / application-name metas."""
    if not html_or_text or "<" not in html_or_text:
        return None
    match = _SITE_NAME_RE.search(html_or_text)
    if not match:
        return None
    value = _strip_tags(match.group(1) or match.group(2) or "")
    if not value or len(value) < 2 or len(value) > 80:
        return None
    if is_weak_web_supplier_name(value):
        brand = brand_from_title(value)
        return brand
    return _prettify_brand(value)


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
        "site_name": extract_site_name(html_or_text),
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
        evidence.append(f"page_title:{title.strip()[:200]}")

    # Prefer shop/distributor name over product title / SERP query / web-uuid.
    url = supplier.url or supplier.contacts.get("website")
    shop_name = (
        str(parsed.get("shop_name")).strip()
        if isinstance(parsed.get("shop_name"), str)
        else None
    )
    site_name = (
        str(parsed.get("site_name")).strip()
        if isinstance(parsed.get("site_name"), str)
        else None
    )
    derived_name = derive_web_supplier_name(
        url=url,
        title=str(title).strip() if isinstance(title, str) else None,
        name=supplier.name,
        shop_name=shop_name,
        site_name=site_name,
    )
    current_name = (supplier.name or "").strip()
    host = hostname_from_url(url)
    enriched_shop = None
    for candidate in (shop_name, site_name):
        cleaned = _prettify_brand(candidate) if candidate else ""
        if cleaned and not is_weak_web_supplier_name(cleaned, url=url):
            enriched_shop = cleaned
            break
    if enriched_shop and (
        is_weak_web_supplier_name(current_name, url=url)
        or (host is not None and current_name.casefold() == host)
    ):
        updates["name"] = enriched_shop[:120]
    elif derived_name and is_weak_web_supplier_name(current_name, url=url):
        updates["name"] = derived_name

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


def _deadline_expired(deadline_monotonic: float | None) -> bool:
    if deadline_monotonic is None:
        return False
    return asyncio.get_running_loop().time() >= float(deadline_monotonic)


async def enrich_web_suppliers(
    suppliers: list[Supplier],
    fetch_provider: PageFetchProvider,
    *,
    concurrency: int | None = None,
    timeout_seconds: float | None = None,
    max_pages: int | None = None,
    product_query: str | None = None,
    chat_fn: ChatFn | None = None,
    only_indexes: set[int] | None = None,
    deadline_monotonic: float | None = None,
) -> list[Supplier]:
    """Fetch each web supplier URL and fill price/city/rating when possible.

    After the isolated headless browser returns HTML/text, Qwen extracts
    structured fields when enabled; regex enrichment is the fallback.
    Failures are skipped (URL kept). Concurrency is capped. Never hangs forever.
    Stops scheduling further page fetches when ``deadline_monotonic`` has passed.
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
    raw_max = (
        max_pages
        if max_pages is not None
        else int(
            os.environ.get("PROCUREMENT_WEB_ENRICH_MAX_PER_BATCH", DEFAULT_ENRICH_MAX_PER_BATCH)
        )
    )
    max_pages = clamp_agent_pages(raw_max) if raw_max > 0 else 0
    semaphore = asyncio.Semaphore(concurrency)
    result = list(suppliers)
    # When only_indexes is set, visit those indexes (agent path); else first max_pages.
    # Always hard-cap visits at MAX_AGENT_PAGES (3).
    allowed = only_indexes
    if allowed is not None and max_pages > 0 and len(allowed) > max_pages:
        allowed = set(sorted(allowed)[:max_pages])
    visited_budget = max_pages if allowed is None else len(allowed)
    stopped_for_deadline = False

    async def _one(index: int, supplier: Supplier) -> None:
        nonlocal stopped_for_deadline
        url = supplier.url or supplier.contacts.get("website")
        if not url or supplier.source != "web":
            return
        if allowed is not None:
            if index not in allowed:
                return
        elif index >= max_pages:
            return
        async with semaphore:
            if _deadline_expired(deadline_monotonic):
                stopped_for_deadline = True
                return
            try:
                emit_progress(f"Открываю {progress_domain(url)}…")
                response = await asyncio.wait_for(
                    fetch_provider.fetch(url),
                    timeout=timeout_seconds,
                )
            except Exception:
                return
        if not isinstance(response, dict) or response.get("status") != "available":
            return
        if _deadline_expired(deadline_monotonic):
            stopped_for_deadline = True
            return
        document = str(response.get("html") or response.get("content") or "")
        if not document.strip():
            return
        emit_progress("Читаю цену и сроки…")
        parsed = await parse_product_page_with_qwen(
            document,
            product_query=product_query,
            page_url=url,
            chat_fn=chat_fn,
        )
        enriched = apply_page_enrichment(supplier, parsed)
        # Mark that the browse agent opened this URL (even if only regex filled fields).
        if allowed is not None:
            evidence = list(enriched.evidence)
            evidence.append("qwen_agent:visited")
            enriched = enriched.model_copy(
                update={"evidence": list(dict.fromkeys(evidence))}
            )
        result[index] = enriched

    await asyncio.gather(
        *[_one(index, supplier) for index, supplier in enumerate(suppliers)],
        return_exceptions=True,
    )
    if stopped_for_deadline:
        emit_progress("Время поиска истекло — останавливаю открытие страниц")
    _ = visited_budget
    return result


def _supplier_serp_hit(supplier: Supplier) -> dict[str, Any]:
    snippet = ""
    for item in supplier.evidence:
        text = str(item)
        if text.startswith("http") or text.startswith("enrichment:"):
            continue
        if text.startswith("qwen_agent:") or text.startswith("match_confidence:"):
            continue
        if text.startswith("page_title:") or text.startswith("delivery:"):
            continue
        if text.startswith("lead_time_days:"):
            continue
        snippet = text
        break
    return {
        "title": supplier.name,
        "name": supplier.name,
        "url": supplier.url or supplier.contacts.get("website") or "",
        "snippet": snippet,
    }


async def run_qwen_browse_agent(
    suppliers: list[Supplier],
    fetch_provider: PageFetchProvider,
    *,
    product_query: str | None = None,
    max_pages: int | None = None,
    concurrency: int | None = None,
    timeout_seconds: float | None = None,
    chat_fn: ChatFn | None = None,
    deadline_monotonic: float | None = None,
) -> tuple[list[Supplier], dict[str, Any]]:
    """Browse+extract agent: pick SERP URLs → fetch → Qwen/regex enrich.

    Always returns the original SERP cards (fail-soft). Diagnostics describe
    what the agent attempted; LM Studio downtime does not drop results.
    """
    diagnostics: dict[str, Any] = {
        "qwen_agent": True,
        "status": "skipped",
        "visited": 0,
        "selected_indexes": [],
        "qwen_enabled": qwen_web_enabled(),
        "gateway_configured": bool(resolve_qwen_gateway_url()) or chat_fn is not None,
    }
    if not suppliers:
        diagnostics["status"] = "empty"
        return [], diagnostics
    if not qwen_agent_enabled():
        diagnostics["status"] = "disabled"
        return list(suppliers), diagnostics
    if _deadline_expired(deadline_monotonic):
        diagnostics["status"] = "deadline"
        diagnostics["message"] = "Время поиска истекло до открытия страниц"
        emit_progress("Время поиска истекло — пропускаю обогащение страниц")
        return list(suppliers), diagnostics

    pages = max_pages if max_pages is not None else qwen_agent_max_pages()
    pages = clamp_agent_pages(pages)
    conc = concurrency if concurrency is not None else qwen_agent_concurrency()
    fetch_timeout = float(
        timeout_seconds
        if timeout_seconds is not None
        else os.environ.get(
            "PROCUREMENT_WEB_QWEN_AGENT_FETCH_TIMEOUT_SECONDS",
            DEFAULT_AGENT_FETCH_TIMEOUT_SECONDS,
        )
    )
    diagnostics["max_pages"] = pages
    diagnostics["concurrency"] = max(1, int(conc))

    web_rows = [
        (index, item)
        for index, item in enumerate(suppliers)
        if item.source == "web" and (item.url or item.contacts.get("website"))
    ]
    if not web_rows:
        diagnostics["status"] = "no_urls"
        return list(suppliers), diagnostics

    hits = [_supplier_serp_hit(item) for _, item in web_rows]
    emit_progress("Qwen выбирает сайты для проверки…")
    try:
        relative = await select_urls_to_visit_with_qwen(
            hits,
            product_query=product_query,
            max_visit=pages,
            chat_fn=chat_fn,
        )
    except Exception as exc:
        logger.info("Qwen browse URL select error: %s", type(exc).__name__)
        relative = list(range(min(pages, len(hits))))
        diagnostics["select_error"] = type(exc).__name__

    absolute_indexes = {
        web_rows[rel][0]
        for rel in relative
        if isinstance(rel, int) and 0 <= rel < len(web_rows)
    }
    if not absolute_indexes:
        absolute_indexes = {web_rows[i][0] for i in range(min(pages, len(web_rows)))}
    # Hard cap visits even if only_indexes was larger (e.g. older callers).
    if len(absolute_indexes) > pages:
        absolute_indexes = set(sorted(absolute_indexes)[:pages])
    diagnostics["selected_indexes"] = sorted(absolute_indexes)

    if not diagnostics["gateway_configured"] and qwen_web_enabled():
        logger.info(
            "Qwen browse agent: gateway not configured — regex enrich + SERP cards kept"
        )
        diagnostics["qwen_skip_reason"] = "gateway_not_configured"

    if _deadline_expired(deadline_monotonic):
        diagnostics["status"] = "deadline"
        diagnostics["message"] = "Время поиска истекло до открытия страниц"
        emit_progress("Время поиска истекло — пропускаю обогащение страниц")
        return list(suppliers), diagnostics

    try:
        enriched = await enrich_web_suppliers(
            suppliers,
            fetch_provider,
            concurrency=max(1, int(conc)),
            timeout_seconds=fetch_timeout,
            max_pages=pages,
            product_query=product_query,
            chat_fn=chat_fn,
            only_indexes=absolute_indexes,
            deadline_monotonic=deadline_monotonic,
        )
    except Exception as exc:
        logger.info("Qwen browse agent enrich failed soft: %s", type(exc).__name__)
        diagnostics["status"] = "enrich_error"
        diagnostics["error"] = type(exc).__name__
        return list(suppliers), diagnostics

    visited = sum(
        1
        for item in enriched
        if any(str(ev) == "qwen_agent:visited" for ev in item.evidence)
    )
    qwen_hits = sum(
        1
        for item in enriched
        if any(str(ev) == "enrichment:qwen" for ev in item.evidence)
    )
    diagnostics["visited"] = visited
    diagnostics["qwen_enriched"] = qwen_hits
    diagnostics["status"] = "completed" if visited else "no_pages_fetched"
    diagnostics["message"] = (
        f"Qwen-агент открыл {visited} стр., извлёк поля через Qwen: {qwen_hits}"
        if visited
        else "Qwen-агент: страницы не открыты, возвращены карточки SERP"
    )
    return enriched, diagnostics


__all__ = [
    "DEFAULT_ENRICH_CONCURRENCY",
    "DEFAULT_ENRICH_TIMEOUT_SECONDS",
    "apply_page_enrichment",
    "brand_from_title",
    "derive_web_supplier_name",
    "enrich_web_suppliers",
    "extract_city",
    "extract_delivery_hint",
    "extract_price",
    "extract_rating",
    "extract_site_name",
    "extract_title",
    "hostname_from_url",
    "is_weak_web_supplier_name",
    "parse_product_page",
    "parse_product_page_with_qwen",
    "run_qwen_browse_agent",
]
