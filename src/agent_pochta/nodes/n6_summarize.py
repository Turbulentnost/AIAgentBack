"""Узел 6. Формирование краткого обзора — раздел 4, узел 6.

LLM генерирует русскоязычный обзор (3–5 предложений): кто прислал, суть,
требуемое действие, важные вложения, срок. Используется как описание задачи в 1С.
"""

from __future__ import annotations

from agent_pochta.services import ServiceContainer
from agent_pochta.state import AgentState


def node_summarize(state: AgentState, container: ServiceContainer) -> AgentState:
    trace = state.get("trace", []) + ["summarize"]
    summary = container.llm.summarize_ru(
        state["email"],
        state.get("combined_text", ""),
        routing=state.get("routing"),
        sender=state.get("sender"),
        attachments_text=state.get("attachments_text", ""),
    )
    return {"summary_ru": summary, "trace": trace}
