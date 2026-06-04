from __future__ import annotations
from app.agents.common.schemas import AgentResult, BaseAgentInput, Finding
class NDControlInput(BaseAgentInput):
    check_change_notices: bool = True
    check_relations: bool = True
NDControlResult = AgentResult
__all__ = ["NDControlInput", "NDControlResult", "AgentResult", "Finding"]
