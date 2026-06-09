from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.agents.tools.schemas import EmptyToolInput, ToolContext, ToolDescriptor
from app.tools.registry import Tool, tool_registry

ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[Any]]


@dataclass(frozen=True)
class AgentToolDefinition:
    name: str
    description: str
    agent_description: str
    handler: ToolHandler | None = None
    input_model: type[BaseModel] | None = None
    output_model: type[BaseModel] | None = None
    required_permissions: list[str] = field(default_factory=list)
    preview_safe: bool = False
    preview_always: bool = False
    preview_default_params: dict[str, Any] = field(default_factory=dict)

    @property
    def input_schema(self) -> dict[str, Any] | None:
        return self.input_model.model_json_schema() if self.input_model is not None else None

    @property
    def output_schema(self) -> dict[str, Any] | None:
        return self.output_model.model_json_schema() if self.output_model is not None else None

    @property
    def implemented(self) -> bool:
        return self.handler is not None

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description=self.description,
            agent_description=self.agent_description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            required_permissions=self.required_permissions,
            implemented=self.implemented,
        )


class AgentToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AgentToolDefinition] = {}

    def register(self, definition: AgentToolDefinition) -> None:
        self._tools[definition.name] = definition
        if definition.handler is not None:
            tool_registry.register(
                Tool(
                    name=definition.name,
                    description=definition.description,
                    handler=self._legacy_handler(definition),
                    input_schema=definition.input_schema,
                    required_permissions=definition.required_permissions,
                )
            )

    def get(self, name: str) -> AgentToolDefinition | None:
        return self._tools.get(name)

    def list(self) -> list[AgentToolDefinition]:
        return list(self._tools.values())

    def descriptors(self) -> list[ToolDescriptor]:
        return [tool.descriptor() for tool in self.list()]

    def _legacy_handler(self, definition: AgentToolDefinition):
        async def handler(**kwargs: Any) -> Any:
            db = kwargs.pop("db")
            user = kwargs.pop("user")
            agent_id = kwargs.pop("agent_id", None)
            task_id = kwargs.pop("task_id", None)
            params = definition.input_model(**kwargs) if definition.input_model is not None else EmptyToolInput()
            context = ToolContext(db=db, user=user, agent_id=agent_id, task_id=task_id)
            assert definition.handler is not None
            return await definition.handler(params, context)

        return handler


agent_tool_registry = AgentToolRegistry()


def register_tool(definition: AgentToolDefinition) -> AgentToolDefinition:
    agent_tool_registry.register(definition)
    return definition
