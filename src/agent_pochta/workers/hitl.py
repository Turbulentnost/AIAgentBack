"""Продолжение пайплайна после human-in-the-loop (серая зона / подтверждение отдела)."""

from __future__ import annotations

from agent_pochta import nodes
from agent_pochta.schemas import RoutingResult, SpamResult
from agent_pochta.services import ServiceContainer
from agent_pochta.state import AgentState


def continue_after_human_approval(
    *,
    email,
    routing: RoutingResult,
    container: ServiceContainer,
    spam: SpamResult | None = None,
) -> AgentState:
    """Узлы 3–4 + 6–8: обзор, 1С, сохранение (маршрутизация уже подтверждена человеком)."""
    state: AgentState = {
        "email": email,
        "routing": routing.model_copy(
            update={
                "confidence": 1.0,
                "reasoning": "Подтверждено оператором",
            }
        ),
        "trace": ["human_approved"],
    }
    if spam is not None:
        state["spam"] = spam

    for fn in (
        nodes.node_identify_sender,
        nodes.node_process_content,
        nodes.node_summarize,
        nodes.node_create_erp_task,
        nodes.node_finalize,
    ):
        patch = fn(state, container=container)
        state = {**state, **patch}

    return state
