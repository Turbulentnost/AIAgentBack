from __future__ import annotations

import html as html_lib
import re
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from app.tools.base import Tool
from app.tools.registry import register_tool
from app.tools.schemas import (
    FetchPageViaUserBrowserInput,
    FetchPageViaUserBrowserOutput,
    ToolContext,
    WebSearchInput,
    WebSearchOutput,
    WebSearchResultItem,
)
from app.schemas.browser_run import BrowserRunCreate
from app.services.browser_runner_service import BrowserRunnerService

SEARCH_ENGINE_HOSTS = {
    "duckduckgo.com",
    "html.duckduckgo.com",
    "www.duckduckgo.com",
    "duck.com",
    "google.com",
    "www.google.com",
    "bing.com",
    "www.bing.com",
    "yandex.ru",
    "ya.ru",
}

_ANCHOR_RE = re.compile(r"<a\b[^>]*?href=\"(?P<href>[^\"]+)\"[^>]*>(?P<text>.*?)</a>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


async def fetch_page_via_user_browser(
    payload: FetchPageViaUserBrowserInput,
    context: ToolContext,
) -> FetchPageViaUserBrowserOutput:
    service = BrowserRunnerService(context.db)
    run = await service.create_run(
        BrowserRunCreate(
            url=payload.url,
            extract_mode=payload.extract_mode,
            reason=payload.reason,
            timeout_seconds=payload.timeout_seconds,
            task_id=context.task_id,
            agent_id=context.agent_id,
        ),
        requested_by_user_id=context.user.id,
        requested_by_agent_id=context.agent_id,
        task_id=context.task_id,
    )
    await context.db.commit()

    completed = await service.wait_for_result(run.id, payload.timeout_seconds)
    return FetchPageViaUserBrowserOutput(
        status=completed.status.value,
        url=completed.url,
        title=completed.title,
        text=completed.result_text,
        html=completed.result_html,
        tables=completed.result_tables or [],
        screenshot_document_id=completed.screenshot_object_name,
        error_message=completed.error_message,
        metadata={
            **(completed.metadata_ or {}),
            "extract_mode": completed.extract_mode,
            "browser_run_id": str(completed.id),
            "finished_at": completed.finished_at.isoformat() if completed.finished_at else None,
        },
    )


def _normalize_search_url(href: str) -> str | None:
    href = href.strip()
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    # DuckDuckGo wraps real links in a redirect: /l/?uddg=<encoded-url>
    if parsed.path.endswith("/l/") or "uddg" in (parsed.query or ""):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            href = unquote(target[0])
            parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower().lstrip("www.")
    bare_host = (parsed.hostname or "").lower()
    if not bare_host or bare_host in SEARCH_ENGINE_HOSTS or host in SEARCH_ENGINE_HOSTS:
        return None
    return href


def _parse_search_results(raw_html: str, max_results: int) -> list[WebSearchResultItem]:
    results: list[WebSearchResultItem] = []
    seen: set[str] = set()
    for match in _ANCHOR_RE.finditer(raw_html or ""):
        url = _normalize_search_url(match.group("href"))
        if not url or url in seen:
            continue
        text = html_lib.unescape(_TAG_RE.sub("", match.group("text"))).strip()
        title = text or urlparse(url).hostname or url
        seen.add(url)
        results.append(WebSearchResultItem(title=title[:300], url=url))
        if len(results) >= max_results:
            break
    return results


async def web_search(payload: WebSearchInput, context: ToolContext) -> WebSearchOutput:
    search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(payload.query)}"
    service = BrowserRunnerService(context.db)
    run = await service.create_run(
        BrowserRunCreate(
            url=search_url,
            extract_mode="html",
            reason=f"Поиск сайтов по запросу: {payload.query}"[:1000],
            timeout_seconds=45,
        ),
        requested_by_user_id=context.user.id,
        requested_by_agent_id=context.agent_id,
        task_id=context.task_id,
    )
    await context.db.commit()

    completed = await service.wait_for_result(run.id, 45)
    raw_html = completed.result_html or completed.result_text or ""
    results = _parse_search_results(raw_html, payload.max_results)
    return WebSearchOutput(
        query=payload.query,
        engine="duckduckgo",
        status=completed.status.value,
        results=results,
        error_message=completed.error_message,
    )


class FetchPageViaUserBrowserTool(Tool):
    name = "fetch_page_via_user_browser"
    description = "Открывает разрешенный URL через браузер пользователя и возвращает извлеченное содержимое."
    agent_description = (
        "Инструмент fetch_page_via_user_browser открывает указанную страницу через браузер пользователя "
        "и возвращает извлеченное содержимое страницы. Используй этот инструмент, если информация доступна "
        "только через пользовательский браузер, корпоративную сеть, VPN, внутренний портал, web-интерфейс 1С "
        "или страницу, требующую пользовательской авторизации. Передавай только конкретный URL и цель "
        "извлечения. Не используй инструмент для произвольного обхода сайтов, массового сканирования или "
        "открытия непроверенных ссылок."
    )
    input_model = FetchPageViaUserBrowserInput
    output_model = FetchPageViaUserBrowserOutput
    required_permissions = ["browser_runs.create"]

    async def execute(
        self, payload: FetchPageViaUserBrowserInput, context: ToolContext
    ) -> FetchPageViaUserBrowserOutput:
        return await fetch_page_via_user_browser(payload, context)


class WebSearchTool(Tool):
    name = "web_search"
    description = "Ищет в поисковике (через браузер пользователя) и возвращает список релевантных сайтов."
    agent_description = (
        "Инструмент web_search выполняет поисковый запрос в поисковой системе через браузер пользователя "
        "и возвращает список найденных сайтов (заголовок и URL). Используй его, когда неизвестно, на каких "
        "сайтах искать информацию: сначала найди подходящие источники через web_search, затем открой "
        "конкретные страницы через fetch_page_via_user_browser. Передавай короткий поисковый запрос."
    )
    input_model = WebSearchInput
    output_model = WebSearchOutput
    required_permissions = ["browser_runs.create"]

    async def execute(self, payload: WebSearchInput, context: ToolContext) -> WebSearchOutput:
        return await web_search(payload, context)


register_tool(FetchPageViaUserBrowserTool())
register_tool(WebSearchTool())
