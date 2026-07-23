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
with `live_data: false`. Web tools use an isolated headless system Yandex
Browser process and Yandex Search. They never open the user's profile or reuse
cookies. Missing browser, CAPTCHA, and timeout conditions return explicit
non-live statuses without fabricated results.

The browser executable is resolved from `YANDEX_BROWSER_PATH`, then the normal
Windows x86, x64, and per-user install locations. Optional limits are:

```powershell
$env:YANDEX_BROWSER_PATH = "C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe"
$env:YANDEX_BROWSER_TIMEOUT_SECONDS = "20"
$env:YANDEX_BROWSER_MAX_PAGE_BYTES = "2000000"
$env:YANDEX_BROWSER_MAX_RESULTS = "20"
$env:YANDEX_BROWSER_REQUEST_TIMEOUT_SECONDS = "25"
$env:PROCUREMENT_MANAGER_INTERNAL_SUPPLIER_THRESHOLD = "1"
$env:PROCUREMENT_MANAGER_SEARCH_TIMEOUT_SECONDS = "30"
```

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
