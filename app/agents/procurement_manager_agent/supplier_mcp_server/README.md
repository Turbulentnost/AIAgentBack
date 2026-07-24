# procurement-supplier-mcp

Standalone MCP stdio server for the procurement manager agent. It implements
JSON-RPC 2.0 `initialize`, `notifications/initialized`, `tools/list`, and
`tools/call` using newline-delimited messages on stdin/stdout.

Run from the `AIAgentBack` directory:

```powershell
py -m app.agents.procurement_manager_agent.supplier_mcp_server
```

On systems where `py` is unavailable, configure the client before starting the
backend:

```powershell
$env:PROCUREMENT_SUPPLIER_MCP_PYTHON = "python"
```

Internal supplier data uses a deterministic fixture and marks every response
with `live_data: false`. Web tools launch an isolated **background** headless
Chromium-family browser (`--headless=new`) with a temporary `--user-data-dir`
and talk to it over Chrome DevTools Protocol. They never open a visible window,
never attach to the user's main Edge/Chrome/Yandex profile, and never reuse
cookies. By default (`PROCUREMENT_WEB_SEARCH_PROVIDER=auto`) search prefers
**Edge or Chrome + Bing HTML** (DuckDuckGo lite/html optional via env), because
Yandex often returns SmartCaptcha in headless mode. The CDP session overrides
the headless User-Agent/locale so Bing does not cloak results; the Yandex
Browser path remains available as an explicit mode or fallback. `--dump-dom`
is intentionally avoided: on Yandex Browser it hangs indefinitely even for
`about:blank`. Missing browser, CAPTCHA, and timeout conditions return explicit
non-live statuses without fabricated results. On timeout the whole browser
process tree is killed (`taskkill /T` on Windows).

Browser / search selection:

```powershell
# Preferred headless browser for DuckDuckGo/Bing (auto-detect Edge then Chrome)
$env:PROCUREMENT_WEB_BROWSER_PATH = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$env:PROCUREMENT_WEB_BROWSER_PREFER = "edge"   # edge|chrome|chromium
$env:PROCUREMENT_WEB_SEARCH_ENGINE = "bing"  # bing|duckduckgo (bing is more reliable headless)
# auto|chromium|yandex|yandex_first
$env:PROCUREMENT_WEB_SEARCH_PROVIDER = "auto"
$env:PROCUREMENT_WEB_BROWSER_TIMEOUT_SECONDS = "30"
$env:PROCUREMENT_WEB_BROWSER_MAX_PAGE_BYTES = "2000000"
$env:PROCUREMENT_WEB_BROWSER_MAX_RESULTS = "20"

# Legacy / explicit Yandex path (still supported)
$env:YANDEX_BROWSER_PATH = "C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe"
$env:YANDEX_BROWSER_TIMEOUT_SECONDS = "30"
$env:YANDEX_BROWSER_MAX_PAGE_BYTES = "2000000"
$env:YANDEX_BROWSER_MAX_RESULTS = "20"
$env:YANDEX_BROWSER_REQUEST_TIMEOUT_SECONDS = "45"
$env:PROCUREMENT_MANAGER_INTERNAL_SUPPLIER_THRESHOLD = "1"
$env:PROCUREMENT_MANAGER_SEARCH_TIMEOUT_SECONDS = "30"

# Qwen (LM Studio OpenAI-compatible) — parse price/city/title from fetched pages
# Default: enabled in ENVIRONMENT=dev|test. Falls back to regex if LLM fails/timeout.
$env:PROCUREMENT_WEB_USE_QWEN = "true"
$env:PROCUREMENT_WEB_QWEN_REFINE_QUERY = "false"   # optional short Russian Bing query
$env:PROCUREMENT_WEB_QWEN_TIMEOUT_SECONDS = "25"
$env:LLM_GATEWAY_URL = "http://192.168.1.157:1234/v1"   # or LLM_GATEWAY_BASE_URL
$env:LLM_DEFAULT_MODEL = "qwen/qwen3.5-9b"
```

After headless Edge/Bing returns a product page, enrichment calls Qwen to fill
`unit_price` / `approx_cost`, `city`, delivery hint / `lead_time_days`, and a short
title (JSON). Qwen is never used to invent suppliers — only to parse real page/SERP
text. If the gateway is down or times out, regex enrichment still runs.

`ProcurementManagerService.search_suppliers` records a `supplier_search` operation
(`running` → `completed` / `failed`) in case metadata and aborts the HTTP wait after
`PROCUREMENT_MANAGER_SEARCH_TIMEOUT_SECONDS`. Clients should poll
`GET .../operations/{operation_id}` instead of holding the request open.

`supplier_fetch_page` accepts only public `http`/`https` URLs. Localhost,
private/link-local/reserved IP literals, and executable download suffixes are
rejected before the browser starts.

Gated tools require an approved `(approval_id, operation)` pair. The default
provider approves nothing. Development tests may configure pairs such as:

```powershell
$env:PROCUREMENT_SUPPLIER_MCP_APPROVALS = "approval-1:send_rfq,approval-2:select_supplier"
```

Even with approval, gated tools return drafts with `executed: false` and
`payment_executed: false`. This server has no payment tool.

The external 1C MCP remains separately configured as `1c-supplier-upstream` in
`supplier_mcp.json`; it is not invoked by the fixture provider and cannot
recursively call this server.
