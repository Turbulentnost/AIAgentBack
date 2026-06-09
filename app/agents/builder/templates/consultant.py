from __future__ import annotations

import re
from typing import Any

CONSULTANT_CONFIDENCE_THRESHOLD = 0.8

KNOWLEDGE_TOOL_HINTS = (
    "get_current_date",
    "search_knowledge_base",
    "list_available_knowledge_bases",
    "get_knowledge_fragment",
    "get_document_text",
    "fetch_page_via_user_browser",
    "get_tool_description",
)

CONSULTANT_FORBIDDEN_ELEMENT_KEYS = frozenset(
    {
        "knowledge_sources",
        "weather_sites",
        "sites",
        "data_sources",
        "sources",
        "output_format",
        "response_format",
        "answer_format",
        "presentation_format",
        "search_approach",
        "answer_prompt",
    }
)

FORBIDDEN_QUESTION_PATTERNS = re.compile(
    r"сайт|источник|url|gismeteo|yandex|weather\.com|"
    r"формат\s+(ответ|вывод|представ)|в\s+каком\s+формате|"
    r"какие\s+.*сайт|какой\s+сайт|какие\s+параметр",
    re.IGNORECASE,
)

CONSULTANT_WORKFLOW_TEMPLATE: list[dict[str, Any]] = [
    {
        "label": "Получение вопроса",
        "capability": "receive_question",
        "goal": "принять вопрос пользователя",
        "node_kind": "task",
    },
    {
        "label": "Поиск в источниках",
        "capability": "knowledge_search",
        "goal": "получить информацию по теме вопроса",
        "node_kind": "capability",
    },
    {
        "label": "RAG-извлечение",
        "capability": "rag_retrieval",
        "goal": "извлечь релевантные фрагменты из источников",
        "node_kind": "capability",
    },
    {
        "label": "Формирование ответа",
        "capability": "llm_answer",
        "goal": "сформировать ответ на основе найденной информации",
        "node_kind": "capability",
    },
    {
        "label": "Показ ответа",
        "capability": "present_answer",
        "goal": "показать ответ пользователю в нужном формате",
        "node_kind": "task",
    },
]


def resolve_knowledge_sources_from_tools(
    tools_catalog: list[dict[str, Any]],
    goal: str = "",
) -> dict[str, Any]:
    implemented = [tool for tool in tools_catalog if tool.get("implemented")]
    goal_lower = goal.lower()

    selected: list[dict[str, Any]] = []
    for name in KNOWLEDGE_TOOL_HINTS:
        match = next((tool for tool in implemented if tool.get("name") == name), None)
        if match:
            selected.append(match)

    for tool in implemented:
        if tool in selected:
            continue
        name = str(tool.get("name") or "").lower()
        description = str(tool.get("description") or "").lower()
        if any(
            token in name or token in description
            for token in ("knowledge", "document", "search", "browse", "база", "документ", "поиск")
        ):
            selected.append(tool)

    if not selected:
        selected = implemented[:5]

    if "погод" in goal_lower and not any(
        t.get("name") == "fetch_page_via_user_browser" for t in selected
    ):
        browse = next(
            (tool for tool in implemented if tool.get("name") == "fetch_page_via_user_browser"),
            None,
        )
        if browse:
            selected.append(browse)

    if any(token in goal_lower for token in ("сегодня", "today", "текущ", "дат")):
        if not any(t.get("name") == "get_current_date" for t in selected):
            date_tool = next(
                (tool for tool in implemented if tool.get("name") == "get_current_date"),
                None,
            )
            if date_tool:
                selected.insert(0, date_tool)

    labels = [
        f"{tool.get('name')}: {tool.get('description') or 'без описания'}"
        for tool in selected
    ]
    return {
        "knowledge_sources": "; ".join(labels) if labels else "доступные инструменты платформы",
        "recommended_tools": [str(tool.get("name")) for tool in selected if tool.get("name")],
        "knowledge_sources_auto": True,
    }


def build_auto_response_format_element() -> dict[str, Any]:
    return {
        "key": "response_format",
        "label": "Формат ответа",
        "question": None,
        "required": False,
        "value": "структурированный текстовый ответ консультанта",
        "status": "filled",
        "confidence": 1.0,
        "auto_resolved": True,
    }


def build_auto_data_sources_element(goal: str) -> dict[str, Any]:
    goal_lower = goal.lower()
    if any(token in goal_lower for token in ("погод", "сайт", "браузер", "browser", "веб")):
        value = (
            "агент сам выберет подходящие сайты и страницы через fetch_page_via_user_browser "
            "и другие доступные инструменты"
        )
    else:
        value = "агент сам выберет источники из доступных инструментов платформы"
    return {
        "key": "data_sources",
        "label": "Источники данных",
        "question": None,
        "required": False,
        "value": value,
        "status": "filled",
        "confidence": 1.0,
        "auto_resolved": True,
    }


def build_auto_knowledge_sources_element(sources_text: str) -> dict[str, Any]:
    return {
        "key": "knowledge_sources",
        "label": "Источники знаний",
        "question": None,
        "required": False,
        "value": sources_text,
        "status": "filled",
        "confidence": 1.0,
        "auto_resolved": True,
    }


def init_consultant_requirements(
    requirements: dict[str, Any] | None,
    tools_catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    merged = dict(requirements or {})
    goal = str(merged.get("goal") or "")
    sources = resolve_knowledge_sources_from_tools(tools_catalog, goal)
    merged.update(sources)

    merged["response_format"] = "структурированный текстовый ответ консультанта"
    merged["search_approach"] = merged.get("search_approach") or "автовыбор инструментов платформы"
    elements = [item for item in (merged.get("required_elements") or []) if isinstance(item, dict)]
    without_auto = [
        item
        for item in elements
        if str(item.get("key") or "") not in CONSULTANT_FORBIDDEN_ELEMENT_KEYS
        and str(item.get("key") or "") != "data_sources"
    ]
    merged["required_elements"] = sanitize_consultant_elements(
        [
            build_auto_knowledge_sources_element(sources["knowledge_sources"]),
            build_auto_data_sources_element(goal),
            build_auto_response_format_element(),
            *without_auto,
        ],
        goal,
    )
    merged["pending_questions"] = filter_consultant_questions(merged.get("pending_questions") or [])
    return merged


def init_consultant_required_elements(
    existing: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Сохранено для обратной совместимости: без фиксированных вопросов пользователю."""
    elements = [item for item in (existing or []) if isinstance(item, dict)]
    if any(item.get("key") == "knowledge_sources" for item in elements):
        return elements
    return elements


def is_forbidden_consultant_element(item: dict[str, Any]) -> bool:
    if item.get("auto_resolved"):
        return False
    key = str(item.get("key") or "").lower()
    if key in CONSULTANT_FORBIDDEN_ELEMENT_KEYS:
        return True
    question = str(item.get("question") or "")
    label = str(item.get("label") or "")
    return bool(FORBIDDEN_QUESTION_PATTERNS.search(f"{label} {question}"))


def filter_consultant_questions(questions: list[str]) -> list[str]:
    result: list[str] = []
    for question in questions:
        text = question.strip()
        if not text or FORBIDDEN_QUESTION_PATTERNS.search(text):
            continue
        if text not in result:
            result.append(text)
    return result


def sanitize_consultant_elements(
    elements: list[dict[str, Any]],
    goal: str = "",
) -> list[dict[str, Any]]:
    del goal
    by_key: dict[str, dict[str, Any]] = {}
    for item in elements:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if is_forbidden_consultant_element(item) and not item.get("auto_resolved"):
            continue
        if key and key in by_key and by_key[key].get("auto_resolved"):
            continue
        if key:
            by_key[key] = item
        else:
            by_key[f"__anon_{len(by_key)}"] = item

    auto_keys = {"knowledge_sources", "data_sources", "response_format"}
    ordered: list[dict[str, Any]] = []
    for key in ("knowledge_sources", "data_sources", "response_format"):
        if key in by_key:
            ordered.append(by_key.pop(key))
    for key, item in by_key.items():
        if key.startswith("__anon_"):
            ordered.append(item)
        elif key not in auto_keys:
            ordered.append(item)
    return ordered


def sanitize_consultant_requirements(requirements: dict[str, Any], goal: str = "") -> dict[str, Any]:
    merged = dict(requirements)
    elements = sanitize_consultant_elements(merged.get("required_elements") or [], goal)
    merged["required_elements"] = elements
    merged["pending_questions"] = filter_consultant_questions(merged.get("pending_questions") or [])
    merged["response_format"] = merged.get("response_format") or "структурированный текстовый ответ консультанта"
    return merged


def element_needs_user_input(item: dict[str, Any], *, threshold: float = CONSULTANT_CONFIDENCE_THRESHOLD) -> bool:
    if is_forbidden_consultant_element(item) and not item.get("auto_resolved"):
        return False
    if item.get("auto_resolved"):
        return False
    if not item.get("required", True):
        return False
    value = item.get("value")
    if value and str(value).strip():
        return False
    confidence = item.get("confidence")
    if confidence is not None and float(confidence) >= threshold:
        return False
    return True
