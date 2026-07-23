from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.agents.procurement_manager_agent.documents import render_rfq_draft
from app.agents.procurement_manager_agent.schemas import (
    ComparisonWeights,
    RFQDraftRequest,
    Supplier,
    SupplierQuote,
)
from app.agents.procurement_manager_agent.scoring import compare_quotes
from app.agents.procurement_manager_agent.supplier_mcp_server.providers import (
    ApprovalProvider,
    BrowserSearchProvider,
    EnvironmentApprovalProvider,
    FixtureSupplierProvider,
    SupplierProvider,
    SystemYandexBrowserProvider,
)


class ToolExecutionError(RuntimeError):
    pass


def _object_schema(
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": True,
    }


TOOL_DEFINITIONS = [
    {
        "name": "supplier_search_internal",
        "description": "Search approved internal/upstream supplier sources.",
        "inputSchema": _object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "category": {"type": ["string", "null"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            ["query"],
        ),
    },
    *[
        {
            "name": name,
            "description": description,
            "inputSchema": _object_schema(
                {"supplier_id": {"type": "string", "minLength": 1}},
                ["supplier_id"],
            ),
        }
        for name, description in (
            ("supplier_get_profile", "Read supplier master-data profile."),
            ("supplier_get_contracts", "Read supplier contracts."),
            ("supplier_get_purchase_history", "Read completed purchase history."),
            ("supplier_get_quality_history", "Read supplier quality history."),
            ("supplier_get_open_orders", "Read open supplier orders."),
            ("supplier_get_goods_in_transit", "Read supplier goods in transit."),
        )
    ],
    {
        "name": "supplier_search_web",
        "description": "Search Yandex using an isolated system Yandex Browser process.",
        "inputSchema": _object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            ["query"],
        ),
    },
    {
        "name": "supplier_fetch_page",
        "description": "Fetch a public http/https page in isolated system Yandex Browser.",
        "inputSchema": _object_schema(
            {"url": {"type": "string", "minLength": 1}},
            ["url"],
        ),
    },
    {
        "name": "quote_parse",
        "description": "Parse a structured quote or a simple textual amount.",
        "inputSchema": _object_schema(
            {
                "quote": {"type": "object"},
                "text": {"type": "string"},
                "supplier_id": {"type": "string"},
                "quote_id": {"type": "string"},
            }
        ),
    },
    {
        "name": "quote_compare",
        "description": "Deterministically score supplier quotes.",
        "inputSchema": _object_schema(
            {
                "quotes": {"type": "array", "items": {"type": "object"}},
                "weights": {"type": "object"},
            },
            ["quotes"],
        ),
    },
    {
        "name": "rfq_render",
        "description": "Render an RFQ draft without sending it.",
        "inputSchema": _object_schema(
            {
                "request": {"type": "object"},
                "suppliers": {"type": "array", "items": {"type": "object"}},
                "case_number": {"type": "string"},
            },
            ["request", "case_number"],
        ),
    },
    *[
        {
            "name": name,
            "description": description,
            "inputSchema": _object_schema(
                {
                    "approval_id": {"type": "string", "minLength": 1},
                    "draft": {"type": "object"},
                },
                ["approval_id", "draft"],
            ),
        }
        for name, description in (
            ("rfq_send_draft", "Validate approval for an RFQ send draft; never sends."),
            ("supplier_select", "Validate approval for supplier selection draft."),
            (
                "purchase_order_create_draft",
                "Validate approval and create an order draft; never orders or pays.",
            ),
        )
    ],
]


class SupplierToolDispatcher:
    _RELATED_TOOLS = {
        "supplier_get_contracts",
        "supplier_get_purchase_history",
        "supplier_get_quality_history",
        "supplier_get_open_orders",
        "supplier_get_goods_in_transit",
    }
    _GATED_OPERATIONS = {
        "rfq_send_draft": "send_rfq",
        "supplier_select": "select_supplier",
        "purchase_order_create_draft": "create_supplier_order",
    }

    def __init__(
        self,
        *,
        supplier_provider: SupplierProvider | None = None,
        browser_provider: BrowserSearchProvider | None = None,
        approval_provider: ApprovalProvider | None = None,
    ) -> None:
        self.supplier_provider = supplier_provider or FixtureSupplierProvider()
        self.browser_provider = browser_provider or SystemYandexBrowserProvider()
        self.approval_provider = approval_provider or EnvironmentApprovalProvider()

    @staticmethod
    def list_tools() -> list[dict[str, Any]]:
        return TOOL_DEFINITIONS

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "supplier_search_internal":
            return await self.supplier_provider.search(
                self._required_string(arguments, "query"),
                self._optional_string(arguments, "category"),
                self._limit(arguments),
            )
        if name == "supplier_get_profile":
            return await self.supplier_provider.profile(
                self._required_string(arguments, "supplier_id")
            )
        if name in self._RELATED_TOOLS:
            return await self.supplier_provider.related(
                self._required_string(arguments, "supplier_id"),
                name,
            )
        if name == "supplier_search_web":
            return await self.browser_provider.search(
                self._required_string(arguments, "query"),
                self._limit(arguments),
            )
        if name == "supplier_fetch_page":
            return await self.browser_provider.fetch(
                self._required_string(arguments, "url")
            )
        if name == "quote_parse":
            return self._parse_quote(arguments)
        if name == "quote_compare":
            return self._compare_quotes(arguments)
        if name == "rfq_render":
            return self._render_rfq(arguments)
        if name in self._GATED_OPERATIONS:
            return await self._gated_draft(name, arguments)
        raise ToolExecutionError(f"Unknown tool: {name}")

    async def _gated_draft(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        operation = self._GATED_OPERATIONS[tool_name]
        approval_id = self._required_string(arguments, "approval_id")
        if not await self.approval_provider.is_approved(approval_id, operation):
            raise ToolExecutionError(
                f"Approved approval_id is required for operation {operation}"
            )
        return {
            "status": "draft",
            "approval_status": "approved",
            "approval_id": approval_id,
            "operation": operation,
            "draft": dict(arguments.get("draft") or {}),
            "executed": False,
            "payment_executed": False,
            "provenance": [
                {
                    "source": "request",
                    "provider": "procurement-supplier-mcp",
                    "live": False,
                }
            ],
            "message": "Approval validated; only a draft was produced.",
        }

    @staticmethod
    def _parse_quote(arguments: dict[str, Any]) -> dict[str, Any]:
        structured = arguments.get("quote")
        if isinstance(structured, dict):
            quote = SupplierQuote.model_validate(structured)
            return {
                "status": "available",
                "quote": quote.model_dump(mode="json"),
                "parser": "structured_validation",
                "provenance": [{"source": "request_payload", "live": False}],
            }
        text = str(arguments.get("text") or "").strip()
        amount_match = re.search(r"(\d+(?:[.,]\d{1,2})?)", text.replace(" ", ""))
        if not text or amount_match is None:
            return {
                "status": "unavailable",
                "reason": "structured_quote_or_parseable_text_required",
                "provenance": [{"source": "request_text", "live": False}],
            }
        try:
            amount = Decimal(amount_match.group(1).replace(",", "."))
        except InvalidOperation as exc:
            raise ToolExecutionError("Quote amount is invalid") from exc
        return {
            "status": "partial",
            "parsed": {
                "quote_id": str(arguments.get("quote_id") or "unassigned"),
                "supplier_id": str(arguments.get("supplier_id") or "unassigned"),
                "total_hint": str(amount),
                "currency": "RUB",
            },
            "parser": "deterministic_amount_hint",
            "requires_human_review": True,
            "provenance": [{"source": "request_text", "live": False}],
        }

    @staticmethod
    def _compare_quotes(arguments: dict[str, Any]) -> dict[str, Any]:
        quotes = [
            SupplierQuote.model_validate(item)
            for item in arguments.get("quotes") or []
        ]
        weights = ComparisonWeights.model_validate(arguments.get("weights") or {})
        comparison = compare_quotes(quotes, weights)
        return {
            "status": "available",
            "comparison": comparison.model_dump(mode="json"),
            "deterministic": True,
            "generated_at": datetime.now(UTC).isoformat(),
            "provenance": [{"source": "request_quotes", "live": False}],
        }

    @staticmethod
    def _render_rfq(arguments: dict[str, Any]) -> dict[str, Any]:
        request = RFQDraftRequest.model_validate(arguments.get("request") or {})
        suppliers = [
            Supplier.model_validate(item)
            for item in arguments.get("suppliers") or []
        ]
        draft = render_rfq_draft(
            request,
            suppliers,
            case_number=str(arguments.get("case_number") or ""),
        )
        return {
            "status": "draft",
            "rfq": draft.model_dump(mode="json"),
            "executed": False,
            "provenance": [{"source": "request_payload", "live": False}],
        }

    @staticmethod
    def _required_string(arguments: dict[str, Any], key: str) -> str:
        value = str(arguments.get(key) or "").strip()
        if not value:
            raise ToolExecutionError(f"{key} is required")
        return value

    @staticmethod
    def _optional_string(arguments: dict[str, Any], key: str) -> str | None:
        value = str(arguments.get(key) or "").strip()
        return value or None

    @staticmethod
    def _limit(arguments: dict[str, Any]) -> int:
        return max(1, min(int(arguments.get("limit") or 10), 50))


__all__ = ["SupplierToolDispatcher", "TOOL_DEFINITIONS", "ToolExecutionError"]
