from __future__ import annotations
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[..., Awaitable[Any]]
    input_schema: dict | None = None
    required_permissions: list[str] = field(default_factory=list)
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)
    def list(self) -> list[Tool]:
        return list(self._tools.values())
    async def call(self, name: str, **kwargs: Any) -> Any:
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"Инструмент '{name}' не зарегистрирован")
        return await tool.handler(**kwargs)
tool_registry = ToolRegistry()
