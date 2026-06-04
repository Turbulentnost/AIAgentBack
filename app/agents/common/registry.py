from __future__ import annotations
from app.agents.common.base import BaseAgent
class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, type[BaseAgent]] = {}
    def register(self, agent_cls: type[BaseAgent]) -> type[BaseAgent]:
        self._agents[agent_cls.agent_id] = agent_cls
        return agent_cls
    def get(self, agent_id: str) -> type[BaseAgent] | None:
        return self._agents.get(agent_id)
    def list_ids(self) -> list[str]:
        return list(self._agents.keys())
agent_registry = AgentRegistry()
