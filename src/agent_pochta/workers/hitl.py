"""Продолжение пайплайна после human-in-the-loop (серая зона / подтверждение отдела)."""

from __future__ import annotations

from collections.abc import Callable

from agent_pochta import nodes
from agent_pochta.schemas import RoutingResult, SpamResult
from agent_pochta.services import ServiceContainer
from agent_pochta.state import AgentState


def _hitl_pipeline(*, has_summary: bool) -> tuple[Callable[..., AgentState], ...]:
    """После подтверждения оператором не перегенерируем обзор, если он уже есть в БД."""
    if has_summary:
        return (
            nodes.node_identify_sender,
            nodes.node_create_erp_task,
            nodes.node_finalize,
        )
    return (
        nodes.node_identify_sender,
        nodes.node_process_content,
        nodes.node_summarize,
        nodes.node_create_erp_task,
        nodes.node_finalize,
    )


def continue_after_human_approval(
    *,
    email,
    routing: RoutingResult,
    container: ServiceContainer,
    spam: SpamResult | None = None,
    summary_ru: str | None = None,
    meta: dict | None = None,
) -> AgentState:
    """Узлы 3–8: обзор (если нужен), 1С, сохранение (маршрут уже подтверждён человеком)."""
    existing_summary = (summary_ru or "").strip()
    state: AgentState = {
        "email": email,
        "routing": routing.model_copy(
            update={
                "confidence": 1.0,
                "reasoning": "Подтверждено оператором",
            }
        ),
        "trace": ["human_approved"],
        "meta": dict(meta or {}),
    }
    if spam is not None:
        state["spam"] = spam
    if existing_summary:
        state["summary_ru"] = existing_summary

    for fn in _hitl_pipeline(has_summary=bool(existing_summary)):
        patch = fn(state, container=container)
        state = {**state, **patch}

    return state
