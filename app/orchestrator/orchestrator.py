from __future__ import annotations
from typing import Any, TypedDict
from app.agents import agent_registry
from app.core.logging import get_logger
logger = get_logger(__name__)
class OrchestratorState(TypedDict, total=False):
    task_id: str
    task_type: str
    input_payload: dict
    plan: list[str]
    results: list[dict]
    final_result: dict
    requires_human_review: bool
class Orchestrator:
    def select_agents(self, task_type: str | None) -> list[str]:
        if task_type == "nd_control":
            return ["nd_control_agent"]
        if task_type == "meeting":
            return ["meeting_agent"]
        if task_type in {"procurement", "procurement_logistics"}:
            return ["procurement_logistics_agent"]
        # Procurement has a strict event contract and must only run explicitly.
        return [
            agent_id
            for agent_id in agent_registry.list_ids()
            if agent_id != "procurement_logistics_agent"
        ]
    async def run(self, task_type: str | None, input_payload: dict) -> dict[str, Any]:
        selected = self.select_agents(task_type)
        results: list[dict] = []
        requires_human = False
        for agent_id in selected:
            agent_cls = agent_registry.get(agent_id)
            if agent_cls is None:
                continue
            result = await agent_cls().run(input_payload)
            results.append(result.model_dump())
            requires_human = requires_human or result.requires_human_review
        return {"results": results, "requires_human_review": requires_human, "summary": f"Выполнено агентов: {len(results)}"}
orchestrator = Orchestrator()
