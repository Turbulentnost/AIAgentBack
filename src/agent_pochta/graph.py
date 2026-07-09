"""Сборка графа агента на LangGraph (ТЗ 3658 + ТЗ-АГТ-ПОЧТА-001).

Маршруты:
  imap_listener → spam_filter (правила + ТЗ §9)
  spam_filter   ─ SPAM → finalize
                └ иначе → identify_sender → process_content → route_department
  route_department — RuleRouter; при низкой уверенности RAG departments + LLM
                   ─ SPAM / серая зона / низкая уверенность → finalize
                   └ ок → create_erp_task → finalize
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from agent_pochta import nodes
from agent_pochta.schemas import ProcessingStatus
from agent_pochta.services import ServiceContainer, build_container
from agent_pochta.state import AgentState


def _route_after_spam(state: AgentState) -> str:
    status = state.get("status")
    if status in (ProcessingStatus.SPAM, ProcessingStatus.AWAITING_HUMAN):
        return "finalize"
    return "identify_sender"


def _route_after_department(state: AgentState) -> str:
    status = state.get("status")
    if status in (ProcessingStatus.SPAM, ProcessingStatus.AWAITING_HUMAN):
        return "finalize"
    return "create_erp_task"


def build_graph(container: ServiceContainer | None = None):
    """Компилирует граф. `container` можно подменить (тесты, реальные сервисы)."""
    container = container or build_container()

    def bind(fn):
        return partial(fn, container=container)

    g = StateGraph(AgentState)

    g.add_node("imap_listener", bind(nodes.node_imap_listener))
    g.add_node("spam_filter", bind(nodes.node_spam_filter))
    g.add_node("identify_sender", bind(nodes.node_identify_sender))
    g.add_node("process_content", bind(nodes.node_process_content))
    g.add_node("route_department", bind(nodes.node_route_department))
    g.add_node("create_erp_task", bind(nodes.node_create_erp_task))
    g.add_node("finalize", bind(nodes.node_finalize))

    g.add_edge(START, "imap_listener")
    g.add_edge("imap_listener", "spam_filter")
    g.add_conditional_edges(
        "spam_filter", _route_after_spam,
        {"identify_sender": "identify_sender", "finalize": "finalize"},
    )
    g.add_edge("identify_sender", "process_content")
    g.add_edge("process_content", "route_department")
    g.add_conditional_edges(
        "route_department", _route_after_department,
        {"create_erp_task": "create_erp_task", "finalize": "finalize"},
    )
    g.add_edge("create_erp_task", "finalize")
    g.add_edge("finalize", END)

    return g.compile()
