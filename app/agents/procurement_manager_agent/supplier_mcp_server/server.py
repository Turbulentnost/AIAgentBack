from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from pydantic import ValidationError

from app.agents.procurement_manager_agent.supplier_mcp_server.tools import (
    SupplierToolDispatcher,
    ToolExecutionError,
)

SERVER_NAME = "procurement-supplier-mcp"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"


class ProcurementSupplierMCPServer:
    def __init__(self, dispatcher: SupplierToolDispatcher | None = None) -> None:
        self.dispatcher = dispatcher or SupplierToolDispatcher()
        self.initialized = False

    async def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        if method == "notifications/initialized":
            self.initialized = True
            return None
        if method == "initialize":
            return self._result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    "instructions": (
                        "Read-first procurement supplier server. Gated tools create "
                        "drafts only and never send, order, or execute payment."
                    ),
                },
            )
        if method == "tools/list":
            return self._result(request_id, {"tools": self.dispatcher.list_tools()})
        if method == "tools/call":
            params = request.get("params") or {}
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return self._tool_error(request_id, "Tool arguments must be an object")
            try:
                payload = await self.dispatcher.call(name, arguments)
            except (ToolExecutionError, ValidationError, ValueError, TypeError) as exc:
                return self._tool_error(request_id, str(exc))
            return self._result(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                payload,
                                ensure_ascii=False,
                                default=str,
                                separators=(",", ":"),
                            ),
                        }
                    ],
                    "isError": False,
                },
            )
        if request_id is None:
            return None
        return self._error(request_id, -32601, f"Method not found: {method}")

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message[:1000]},
        }

    @classmethod
    def _tool_error(cls, request_id: Any, message: str) -> dict[str, Any]:
        return cls._result(
            request_id,
            {
                "content": [{"type": "text", "text": message[:1000]}],
                "isError": True,
            },
        )


async def serve_stdio(server: ProcurementSupplierMCPServer | None = None) -> None:
    mcp_server = server or ProcurementSupplierMCPServer()
    while True:
        line = await asyncio.to_thread(sys.stdin.buffer.readline)
        if not line:
            return
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("JSON-RPC request must be an object")
            response = await mcp_server.handle(request)
        except (json.JSONDecodeError, ValueError) as exc:
            response = mcp_server._error(None, -32700, str(exc))
        if response is not None:
            encoded = json.dumps(
                response,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            )
            sys.stdout.write(encoded + "\n")
            sys.stdout.flush()


def main() -> None:
    asyncio.run(serve_stdio())


if __name__ == "__main__":
    main()


__all__ = [
    "ProcurementSupplierMCPServer",
    "main",
    "serve_stdio",
]
