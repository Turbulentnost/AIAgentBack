from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).with_name("mcp1C.json")


class MCPUnavailableError(RuntimeError):
    pass


class MCPCallError(RuntimeError):
    pass


class OneCMCPClient:
    """Small stdio MCP adapter used until the platform gains a shared MCP client."""

    def __init__(
        self,
        *,
        config_path: Path = CONFIG_PATH,
        server_name: str = "1c-odata",
        timeout_seconds: float | None = None,
        max_attempts: int = 2,
    ) -> None:
        self.config_path = config_path
        self.server_name = server_name
        self.max_attempts = max(1, max_attempts)
        self._config = self._load_server_config()
        configured_timeout = int((self._config.get("env") or {}).get("ODATA_TIMEOUT_MS", "120000"))
        self.timeout_seconds = timeout_seconds or max(1.0, configured_timeout / 1000)
        self._tools_cache: list[dict[str, Any]] | None = None

    def configured_capabilities(self) -> dict[str, str]:
        raw = self._config.get("capabilityMap") or {}
        return {str(key): str(value) for key, value in raw.items()}

    async def list_tools(self) -> list[dict[str, Any]]:
        if self._tools_cache is None:
            self._tools_cache = await self._with_retry("tools/list", {})
        return self._tools_cache

    async def call_capability(self, capability: str, arguments: dict[str, Any]) -> Any:
        tools = await self.list_tools()
        available_names = {
            str(item.get("name"))
            for item in tools
            if isinstance(item, dict) and item.get("name")
        }
        configured_name = self.configured_capabilities().get(capability)
        tool_name = configured_name or (capability if capability in available_names else None)
        if tool_name is None or tool_name not in available_names:
            raise MCPUnavailableError(
                f"Read-only 1C capability '{capability}' is not configured "
                "by the connected MCP server"
            )
        result = await self._with_retry(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )
        return _decode_tool_result(result)

    async def _with_retry(self, method: str, params: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await asyncio.wait_for(
                    self._request(method, params),
                    timeout=self.timeout_seconds,
                )
            except (TimeoutError, OSError, MCPUnavailableError) as exc:
                last_error = exc
                if attempt < self.max_attempts:
                    await asyncio.sleep(min(0.25 * attempt, 1.0))
        raise MCPUnavailableError("1C MCP transport is unavailable") from last_error

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        command = self._config.get("command")
        if not command:
            raise MCPUnavailableError("1C MCP command is not configured")
        args = [str(value) for value in self._config.get("args") or []]
        env = os.environ.copy()
        env.update({str(key): str(value) for key, value in (self._config.get("env") or {}).items()})
        try:
            process = await asyncio.create_subprocess_exec(
                str(command),
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
                limit=8 * 1024 * 1024,
            )
        except OSError as exc:
            raise MCPUnavailableError("Unable to start configured 1C MCP server") from exc

        try:
            await self._send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "procurement-logistics-agent", "version": "0.2.0"},
                    },
                },
            )
            await self._read_response(process, 1)
            await self._send(
                process,
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            )
            await self._send(
                process,
                {"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
            )
            response = await self._read_response(process, 2)
            if "error" in response:
                raise MCPCallError(_safe_mcp_error(response["error"]))
            result = response.get("result")
            if method == "tools/list":
                return list((result or {}).get("tools") or [])
            return result
        finally:
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except TimeoutError:
                    process.kill()
                    await process.wait()

    def _load_server_config(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            config = (raw.get("mcpServers") or {}).get(self.server_name)
        except (OSError, json.JSONDecodeError) as exc:
            raise MCPUnavailableError("1C MCP configuration cannot be loaded") from exc
        if not isinstance(config, dict):
            raise MCPUnavailableError(f"1C MCP server '{self.server_name}' is not configured")
        resolved = dict(config)
        resolved["command"] = _resolve_env_reference(str(config.get("command") or ""))
        resolved["args"] = [
            _resolve_env_reference(str(value))
            for value in config.get("args") or []
        ]
        resolved["env"] = {
            str(key): _resolve_env_reference(str(value))
            for key, value in (config.get("env") or {}).items()
        }
        return resolved

    @staticmethod
    async def _send(process: asyncio.subprocess.Process, payload: dict[str, Any]) -> None:
        if process.stdin is None:
            raise MCPUnavailableError("1C MCP stdin is unavailable")
        process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        await process.stdin.drain()

    @staticmethod
    async def _read_response(
        process: asyncio.subprocess.Process,
        request_id: int,
    ) -> dict[str, Any]:
        if process.stdout is None:
            raise MCPUnavailableError("1C MCP stdout is unavailable")
        while True:
            line = await process.stdout.readline()
            if not line:
                raise MCPUnavailableError("1C MCP server stopped before returning a response")
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("id") == request_id:
                return payload


def _decode_tool_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    if result.get("isError"):
        content = result.get("content")
        message = next(
            (
                str(item.get("text"))
                for item in content or []
                if isinstance(item, dict)
                and item.get("type") == "text"
                and item.get("text")
            ),
            "1C MCP business tool returned an error",
        )
        raise MCPCallError(message[:1000])
    content = result.get("content")
    if not isinstance(content, list):
        return result
    decoded: list[Any] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue
        try:
            decoded.append(json.loads(text))
        except json.JSONDecodeError:
            decoded.append(text)
    if len(decoded) == 1:
        return decoded[0]
    return decoded


def _safe_mcp_error(error: Any) -> str:
    if isinstance(error, dict):
        code = error.get("code")
        return f"1C MCP request failed (code={code})"
    return "1C MCP request failed"


_ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}$")


def _resolve_env_reference(value: str) -> str:
    match = _ENV_REFERENCE.fullmatch(value)
    if match is None:
        return value
    variable, default = match.groups()
    return os.environ.get(variable, default or "")


__all__ = ["MCPCallError", "MCPUnavailableError", "OneCMCPClient"]
