from __future__ import annotations

import re
from typing import Any

from app.models.enums import AgentType

CAPABILITY_CATALOG: dict[str, dict[str, Any]] = {
    "receive_question": {
        "id": "receive_question",
        "label": "Получение вопроса",
        "description": "Принять и нормализовать вопрос пользователя",
        "suggested_tools": ["get_current_date"],
        "agent_types": [AgentType.CONSULTANT.value],
    },
    "knowledge_search": {
        "id": "knowledge_search",
        "label": "Поиск в источниках",
        "description": "Найти информацию в доступных источниках знаний",
        "suggested_tools": [
            "search_knowledge_base",
            "list_available_knowledge_bases",
            "fetch_page_via_user_browser",
            "get_current_date",
        ],
        "agent_types": [AgentType.CONSULTANT.value],
    },
    "rag_retrieval": {
        "id": "rag_retrieval",
        "label": "RAG-извлечение",
        "description": "Извлечь релевантные фрагменты для ответа",
        "suggested_tools": ["search_knowledge_base", "get_knowledge_fragment"],
        "agent_types": [AgentType.CONSULTANT.value],
    },
    "llm_answer": {
        "id": "llm_answer",
        "label": "Формирование ответа",
        "description": "Сформировать ответ на основе найденной информации",
        "suggested_tools": [],
        "agent_types": [AgentType.CONSULTANT.value],
    },
    "present_answer": {
        "id": "present_answer",
        "label": "Показ ответа",
        "description": "Представить ответ пользователю в нужном формате",
        "suggested_tools": [],
        "agent_types": [AgentType.CONSULTANT.value],
    },
}

TYPE_CONFIRMATION_PATTERNS = (
    r"^да\b",
    r"подтверж",
    r"соглас",
    r"консультант",
    r"верно",
    r"правильно",
    r"ок\b",
    r"okay",
)

ACTION_KEYWORDS = (
    "создай",
    "создать",
    "поставь задач",
    "задачу в 1с",
    "совещан",
    "запланир",
    "отправь",
    "выполни действие",
    "зарегистрир",
    "оформи заявк",
)

CONSULTANT_KEYWORDS = (
    "ответ",
    "вопрос",
    "информац",
    "консультац",
    "поиск",
    "найди",
    "расскаж",
    "объясни",
    "база знан",
    "rag",
    "браузер",
    "browser",
    "сайт",
    "просмотр",
    "вывест",
    "показ",
    "погод",
    "сегодня",
    "извлеч",
    "получ",
)


def get_capability(capability_id: str) -> dict[str, Any] | None:
    return CAPABILITY_CATALOG.get(capability_id)


def get_capability_label(capability_id: str) -> str:
    item = CAPABILITY_CATALOG.get(capability_id)
    return item["label"] if item else capability_id


def resolve_suggested_tools(capability_id: str) -> list[str]:
    item = CAPABILITY_CATALOG.get(capability_id)
    if not item:
        return []
    return list(item.get("suggested_tools") or [])


def is_type_confirmation_message(message: str) -> bool:
    text = message.strip().lower()
    if not text:
        return False
    return any(re.search(pattern, text) for pattern in TYPE_CONFIRMATION_PATTERNS)


def heuristic_classify_agent_type(goal: str, conversation: list[dict[str, str]] | None = None) -> AgentType:
    text = goal.lower()
    if conversation:
        text += " " + " ".join(
            str(item.get("content", ""))
            for item in conversation
            if isinstance(item, dict)
        ).lower()
    action_score = sum(1 for keyword in ACTION_KEYWORDS if keyword in text)
    consultant_score = sum(1 for keyword in CONSULTANT_KEYWORDS if keyword in text)
    if action_score > consultant_score:
        return AgentType.ACTION
    return AgentType.CONSULTANT


def render_capability_workflow_graph(
    steps: list[dict[str, Any]],
    *,
    human_approval: bool = False,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [{"id": "start", "label": "Старт", "type": "start", "node_kind": "task"}]
    edges: list[dict[str, str]] = []
    prev = "start"
    for index, step in enumerate(steps, start=1):
        node_id = f"step_{index}"
        capability = step.get("capability")
        nodes.append(
            {
                "id": node_id,
                "label": step.get("label") or get_capability_label(str(capability or "")),
                "type": "step",
                "node_kind": step.get("node_kind") or ("capability" if capability else "task"),
                "capability": capability,
                "goal": step.get("goal"),
            }
        )
        edges.append({"source": prev, "target": node_id})
        prev = node_id
    if human_approval:
        approval_id = f"step_{len(steps) + 1}"
        nodes.append(
            {
                "id": approval_id,
                "label": "Согласование с пользователем",
                "type": "step",
                "node_kind": "human_approval",
                "capability": "human_approval",
                "goal": "получить подтверждение пользователя",
            }
        )
        edges.append({"source": prev, "target": approval_id})
        prev = approval_id
    nodes.append({"id": "end", "label": "Завершение", "type": "end", "node_kind": "task"})
    edges.append({"source": prev, "target": "end"})
    return {"nodes": nodes, "edges": edges}


def collect_runtime_tool_hints(steps: list[dict[str, Any]]) -> list[str]:
    tools: list[str] = []
    for step in steps:
        capability = step.get("capability")
        if not capability:
            continue
        for tool_name in resolve_suggested_tools(str(capability)):
            if tool_name not in tools:
                tools.append(tool_name)
    return tools
