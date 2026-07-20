from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.common.state import BaseAgentState
from app.agents.tasks_agent.table_presenter import build_tasks_table
from app.core.logging import get_logger

logger = get_logger(__name__)


class TasksState(BaseAgentState, total=False):
    period_start: str | None
    period_end: str | None
    limit: int
    backend: Any
    current_user: Any
    porucheniya: list[dict]
    protocols: list[dict]
    protocol_tasks: list[dict]
    tasks_table: dict[str, Any]
    task_items: list[dict]
    counts: dict[str, int]
    priority_summary: dict[str, int]
    warnings: list[str]
    status: str
    requires_user_review: bool


async def validate_input(state: TasksState) -> dict:
    logger.info("tasks.validate_input", period_start=state.get("period_start"), period_end=state.get("period_end"))
    return {"status": "submitted", "warnings": state.get("warnings", [])}


async def load_porucheniya(state: TasksState) -> dict:
    logger.info("tasks.load_porucheniya")
    backend = state.get("backend")
    if backend is None:
        return {}
    payload = await backend.load_porucheniya(
        period_start=state.get("period_start"),
        period_end=state.get("period_end"),
        limit=state.get("limit") or 500,
        current_user=state.get("current_user"),
    )
    porucheniya = payload.get("porucheniya") or []
    protocols = payload.get("protocols") or []
    protocol_tasks = payload.get("protocol_tasks") or []
    task_items = payload.get("items") or []
    counts = payload.get("counts") or {}
    return {
        "porucheniya": porucheniya,
        "protocols": protocols,
        "protocol_tasks": protocol_tasks,
        "task_items": task_items,
        "counts": counts,
        "period_start": payload.get("period_start"),
        "period_end": payload.get("period_end"),
        "status": "loaded",
    }


async def build_tasks_table(state: TasksState) -> dict:
    logger.info("tasks.build_tasks_table")
    table = build_tasks_table(
        state.get("porucheniya") or [],
        state.get("protocols") or [],
    )
    return {"tasks_table": table, "status": "table_built"}


async def summarize_priorities(state: TasksState) -> dict:
    logger.info("tasks.summarize_priorities")
    summary: dict[str, int] = {}
    for item in state.get("task_items") or []:
        priority = str(item.get("priority") or "unknown")
        summary[priority] = summary.get(priority, 0) + 1
    return {"priority_summary": summary, "status": "summarized"}


async def save_result(state: TasksState) -> dict:
    logger.info("tasks.save_result")
    counts = state.get("counts") or {}
    documents_count = counts.get("porucheniya_documents", len(state.get("porucheniya") or []))
    porucheniya_tasks_count = counts.get("porucheniya_tasks", 0)
    protocol_documents_count = counts.get("protocol_documents", len(state.get("protocols") or []))
    protocol_tasks_count = counts.get("protocol_tasks", 0)
    return {
        "summary": (
            f"Загружено: поручений {documents_count} "
            f"({porucheniya_tasks_count} мероприятий), "
            f"протоколов {protocol_documents_count} "
            f"({protocol_tasks_count} задач)"
        ),
        "status": "completed",
        "requires_human_review": False,
    }


async def wait_user_review(state: TasksState) -> dict:
    logger.info("tasks.wait_user_review")
    return {"requires_user_review": True}


NODE_SEQUENCE = [
    ("validate_input", validate_input),
    ("load_porucheniya", load_porucheniya),
    ("build_tasks_table", build_tasks_table),
    ("summarize_priorities", summarize_priorities),
    ("save_result", save_result),
    ("wait_user_review", wait_user_review),
]


def build_graph():
    graph = StateGraph(TasksState)
    for name, fn in NODE_SEQUENCE:
        graph.add_node(name, fn)
    graph.add_edge(START, "validate_input")
    graph.add_edge("validate_input", "load_porucheniya")
    graph.add_edge("load_porucheniya", "build_tasks_table")
    graph.add_edge("build_tasks_table", "summarize_priorities")
    graph.add_edge("summarize_priorities", "save_result")
    graph.add_edge("save_result", "wait_user_review")
    graph.add_edge("wait_user_review", END)
    return graph.compile()
