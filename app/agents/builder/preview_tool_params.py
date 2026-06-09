from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from app.tools.registry import tool_registry


def _text_chunks(goal: str, requirements: dict[str, Any]) -> list[str]:
    chunks = [goal]
    for item in requirements.get("required_elements") or []:
        if isinstance(item, dict) and item.get("value"):
            chunks.append(str(item["value"]))
    for item in requirements.get("conversation") or []:
        if isinstance(item, dict) and item.get("content"):
            chunks.append(str(item["content"]))
    for key in ("inputs", "outputs", "constraints", "knowledge_sources"):
        value = requirements.get(key)
        if isinstance(value, str):
            chunks.append(value)
    return chunks


def find_http_url(goal: str, requirements: dict[str, Any]) -> str | None:
    for text in _text_chunks(goal, requirements):
        match = re.search(r"https?://[^\s\]>\"']+", text)
        if match:
            return match.group(0).rstrip(".,;)")
    return None


def extract_location(goal: str, requirements: dict[str, Any]) -> str | None:
    for item in requirements.get("required_elements") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").lower()
        label = str(item.get("label") or "").lower()
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        if any(token in f"{key} {label}" for token in ("город", "регион", "локац", "location", "city")):
            return _normalize_city(value)
    combined = " ".join(_text_chunks(goal, requirements)).lower()
    match = re.search(
        r"(ростов(?:е)?(?:\s|-)+на(?:\s|-)+дону|москв[ае]?|санкт(?:\s|-)+петербург|спб|казан[ьи])",
        combined,
    )
    if match:
        return _normalize_city(match.group(1))
    return None


def _normalize_city(value: str) -> str:
    lowered = value.lower()
    if "ростов" in lowered:
        return "Ростов-на-Дону"
    if "москв" in lowered:
        return "Москва"
    if "санкт" in lowered or "спб" in lowered:
        return "Санкт-Петербург"
    if "казан" in lowered:
        return "Казань"
    return value.strip()[:120]


def infer_preview_tool_params(tool_name: str, goal: str, requirements: dict[str, Any]) -> dict[str, Any] | None:
    definition = tool_registry.get(tool_name)
    if definition and definition.preview_default_params:
        return dict(definition.preview_default_params)

    if tool_name == "get_current_date":
        return {"timezone": "Europe/Moscow"}
    if tool_name == "list_available_knowledge_bases":
        return {"query": None}
    if tool_name == "search_knowledge_base":
        query = goal.strip()[:500]
        if not query:
            return None
        return {"query": query, "limit": 5}
    if tool_name == "web_search":
        query = goal.strip()[:400]
        if not query:
            return None
        return {"query": query, "max_results": 8}
    if tool_name == "fetch_page_via_user_browser":
        url = find_http_url(goal, requirements)
        if url:
            return {
                "url": url,
                "reason": goal[:200],
                "extract_mode": "text",
                "timeout_seconds": 45,
            }
        return None
    return None


def has_substantive_preview_data(tool_results: dict[str, Any]) -> bool:
    for name, payload in tool_results.items():
        if name == "get_current_date" or not payload:
            continue
        if not isinstance(payload, dict):
            return True
        if payload.get("text") or payload.get("html"):
            return True
        items = payload.get("items")
        if isinstance(items, list) and items:
            return True
        results = payload.get("results")
        if isinstance(results, list) and results:
            return True
        if payload.get("error_message"):
            continue
        if len(payload) > 1:
            return True
    return False
