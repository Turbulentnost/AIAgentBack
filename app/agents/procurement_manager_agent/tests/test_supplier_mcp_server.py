from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.procurement_agent.mcp_client import OneCMCPClient
from app.agents.procurement_manager_agent.supplier_mcp_server.providers import (
    EnvironmentApprovalProvider,
    WaitingBrowserProvider,
)
from app.agents.procurement_manager_agent.supplier_mcp_server.server import (
    ProcurementSupplierMCPServer,
)
from app.agents.procurement_manager_agent.supplier_mcp_server.tools import (
    SupplierToolDispatcher,
)

EXPECTED_TOOLS = {
    "supplier_search_internal",
    "supplier_get_profile",
    "supplier_get_contracts",
    "supplier_get_purchase_history",
    "supplier_get_quality_history",
    "supplier_get_open_orders",
    "supplier_get_goods_in_transit",
    "supplier_search_web",
    "supplier_fetch_page",
    "quote_parse",
    "quote_compare",
    "rfq_render",
    "rfq_send_draft",
    "supplier_select",
    "purchase_order_create_draft",
}
CONFIG_PATH = Path(__file__).parents[1] / "supplier_mcp.json"


def _request(request_id: int, method: str, params: dict | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }


def _tool_payload(response: dict) -> dict:
    content = response["result"]["content"]
    return json.loads(content[0]["text"])


@pytest.mark.asyncio
async def test_initialize_and_tool_catalog() -> None:
    server = ProcurementSupplierMCPServer()
    initialized = await server.handle(_request(1, "initialize"))
    assert initialized is not None
    assert initialized["result"]["serverInfo"]["name"] == "procurement-supplier-mcp"
    assert initialized["result"]["capabilities"]["tools"]["listChanged"] is False

    notification = await server.handle(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    )
    assert notification is None
    assert server.initialized is True

    listed = await server.handle(_request(2, "tools/list"))
    assert listed is not None
    names = {item["name"] for item in listed["result"]["tools"]}
    assert names == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_fixture_read_call_has_non_live_provenance() -> None:
    server = ProcurementSupplierMCPServer()
    response = await server.handle(
        _request(
            3,
            "tools/call",
            {
                "name": "supplier_search_internal",
                "arguments": {"query": "сталь", "limit": 5},
            },
        )
    )
    assert response is not None
    assert response["result"]["isError"] is False
    payload = _tool_payload(response)
    assert payload["status"] == "available"
    assert payload["live_data"] is False
    assert payload["provider"] == "deterministic_fixture"
    assert payload["items"][0]["supplier_id"] == "fixture-steel"
    assert payload["provenance"][0]["source"] == "fixture"


@pytest.mark.asyncio
async def test_web_tool_returns_controlled_waiting_browser() -> None:
    server = ProcurementSupplierMCPServer(
        SupplierToolDispatcher(browser_provider=WaitingBrowserProvider())
    )
    response = await server.handle(
        _request(
            4,
            "tools/call",
            {
                "name": "supplier_search_web",
                "arguments": {"query": "rare component", "limit": 10},
            },
        )
    )
    assert response is not None
    payload = _tool_payload(response)
    assert payload["status"] == "waiting_browser"
    assert payload["live_data"] is False
    assert payload["items"] == []


@pytest.mark.asyncio
async def test_fetch_tool_calls_injected_browser_provider() -> None:
    class Browser(WaitingBrowserProvider):
        async def fetch(self, url: str) -> dict:
            return {"status": "available", "url": url, "live_data": True}

    server = ProcurementSupplierMCPServer(
        SupplierToolDispatcher(browser_provider=Browser())
    )
    response = await server.handle(
        _request(
            8,
            "tools/call",
            {
                "name": "supplier_fetch_page",
                "arguments": {"url": "https://example.com/catalog"},
            },
        )
    )
    assert response is not None
    assert _tool_payload(response)["url"] == "https://example.com/catalog"


@pytest.mark.asyncio
async def test_gated_tools_reject_and_only_return_approved_drafts() -> None:
    unapproved = ProcurementSupplierMCPServer()
    missing = await unapproved.handle(
        _request(
            5,
            "tools/call",
            {"name": "rfq_send_draft", "arguments": {"draft": {"rfq_id": "rfq-1"}}},
        )
    )
    assert missing is not None
    assert missing["result"]["isError"] is True
    assert "approval_id is required" in missing["result"]["content"][0]["text"]

    rejected = await unapproved.handle(
        _request(
            6,
            "tools/call",
            {
                "name": "rfq_send_draft",
                "arguments": {
                    "approval_id": "not-approved",
                    "draft": {"rfq_id": "rfq-1"},
                },
            },
        )
    )
    assert rejected is not None
    assert rejected["result"]["isError"] is True

    dispatcher = SupplierToolDispatcher(
        approval_provider=EnvironmentApprovalProvider(
            {("approval-1", "send_rfq")}
        )
    )
    approved = ProcurementSupplierMCPServer(dispatcher)
    response = await approved.handle(
        _request(
            7,
            "tools/call",
            {
                "name": "rfq_send_draft",
                "arguments": {
                    "approval_id": "approval-1",
                    "draft": {"rfq_id": "rfq-1"},
                },
            },
        )
    )
    assert response is not None
    payload = _tool_payload(response)
    assert payload["status"] == "draft"
    assert payload["executed"] is False
    assert payload["payment_executed"] is False


@pytest.mark.asyncio
async def test_configured_stdio_client_roundtrip() -> None:
    client = OneCMCPClient(
        config_path=CONFIG_PATH,
        server_name="supplier-read",
        timeout_seconds=15,
        max_attempts=1,
    )
    tools = await client.list_tools()
    assert EXPECTED_TOOLS == {item["name"] for item in tools}
    result = await client.call_capability(
        "read_supplier_search",
        {"query": "комплектующие", "limit": 2},
    )
    assert result["provider"] == "deterministic_fixture"
    assert result["live_data"] is False
