from __future__ import annotations

from app.agents.common.base import BaseAgent


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, type[BaseAgent]] = {}
        self._bootstrapped = False

    def _bootstrap(self) -> None:
        if self._bootstrapped:
            return
        from app.agents.nd_control_agent import department_tools as _department_tools  # noqa: F401
        from app.agents.nd_control_agent import service as _service  # noqa: F401

        _ = (_department_tools, _service)
        self._bootstrapped = True

    def register(self, agent_cls: type[BaseAgent]) -> type[BaseAgent]:
        self._agents[agent_cls.agent_id] = agent_cls
        return agent_cls

    def get(self, agent_id: str) -> type[BaseAgent] | None:
        self._bootstrap()
        return self._agents.get(agent_id)

    def list_ids(self) -> list[str]:
        self._bootstrap()
        return list(self._agents.keys())


agent_registry = AgentRegistry()
