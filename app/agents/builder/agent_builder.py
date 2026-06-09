from __future__ import annotations

from typing import Any

from app.agents.builder.graph import build_graph
from app.agents.builder.state import AgentBuilderState
from app.core.logging import get_logger

logger = get_logger(__name__)

_compiled_graph = None


def get_builder_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


class AgentBuilderRuntime:
    """Runtime adapter for platform agent registry."""

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        return await run_builder_session(**kwargs)


async def run_builder_session(
    *,
    session_id: str,
    goal: str,
    service: Any,
    current_user: Any,
    user_message: str | None = None,
    collected_requirements: dict | None = None,
) -> dict[str, Any]:
    graph = get_builder_graph()
    requirements = collected_requirements or {}
    conversation = requirements.get("conversation") if isinstance(requirements.get("conversation"), list) else []
    initial: AgentBuilderState = {
        "session_id": session_id,
        "goal": goal,
        "user_message": user_message,
        "service": service,
        "current_user": current_user,
        "collected_requirements": requirements,
        "conversation": conversation,
        "assistant_messages": [],
        "clarifying_questions": [],
        "plan_steps": [],
        "current_step_index": 0,
        "requires_user_input": False,
    }
    logger.info("builder.run_session", session_id=session_id)
    result = await graph.ainvoke(initial)
    return dict(result)
