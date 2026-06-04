from __future__ import annotations
from app.agents.common.base import BaseAgent
from app.agents.common.registry import agent_registry
from app.agents.common.schemas import AgentResult
from app.agents.nd_control_agent import config
from app.agents.nd_control_agent.graph import build_graph
from app.agents.nd_control_agent.schemas import NDControlInput
from app.agents.nd_control_agent.tools import TOOL_NAMES
from app.core.logging import get_logger
logger = get_logger(__name__)

@agent_registry.register
class NDControlAgent(BaseAgent):
    agent_id = config.AGENT_ID
    name = config.AGENT_NAME
    version = config.AGENT_VERSION
    allowed_tools = TOOL_NAMES
    def __init__(self) -> None:
        self._graph = build_graph()
    async def run(self, payload: dict) -> AgentResult:
        data = NDControlInput(**payload)
        final_state = await self._graph.ainvoke({"task_id": data.task_id, "document_ids": data.document_ids, "user_id": data.user_id, "findings": []})
        return AgentResult(agent_id=self.agent_id, status="completed", summary=final_state.get("summary", ""), findings=final_state.get("findings", []), data_confidence=final_state.get("data_confidence", "medium"), requires_human_review=final_state.get("requires_human_review", False))

async def run_nd_control_agent(task_id: str) -> AgentResult:
    logger.info("nd_control.run", task_id=task_id)
    agent = NDControlAgent()
    return await agent.run({"task_id": task_id, "document_ids": []})
