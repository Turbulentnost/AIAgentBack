from __future__ import annotations

from app.tools.browser_tools import _normalize_search_url, _parse_search_results


def test_normalize_search_url_decodes_ddg_redirect():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.gismeteo.ru%2Fweather-rostov%2F&rut=abc"
    assert _normalize_search_url(href) == "https://www.gismeteo.ru/weather-rostov/"


def test_normalize_search_url_skips_search_engine_hosts():
    assert _normalize_search_url("https://duckduckgo.com/about") is None
    assert _normalize_search_url("javascript:void(0)") is None


def test_parse_search_results_extracts_links():
    html = """
    <div>
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwttr.in%2FRostov">Погода Ростов wttr</a>
      <a class="result__a" href="https://www.gismeteo.ru/weather-rostov/">Гисметео Ростов</a>
      <a href="https://duckduckgo.com/settings">internal</a>
      <a class="result__a" href="https://www.gismeteo.ru/weather-rostov/">duplicate</a>
    </div>
    """
    results = _parse_search_results(html, max_results=5)
    urls = [item.url for item in results]
    assert "https://wttr.in/Rostov" in urls
    assert "https://www.gismeteo.ru/weather-rostov/" in urls
    assert all("duckduckgo.com" not in url for url in urls)
    # deduplicated
    assert len(urls) == len(set(urls))


def test_parse_search_results_respects_limit():
    anchors = "".join(
        f'<a class="result__a" href="https://example{i}.com/">site {i}</a>' for i in range(10)
    )
    results = _parse_search_results(anchors, max_results=3)
    assert len(results) == 3
