from __future__ import annotations

from app.tools.base import Tool
from app.tools.schemas import ToolDescriptor


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def descriptors(self) -> list[ToolDescriptor]:
        return [tool.descriptor() for tool in self.list()]


tool_registry = ToolRegistry()


def register_tool(tool: Tool) -> Tool:
    tool_registry.register(tool)
    return tool
