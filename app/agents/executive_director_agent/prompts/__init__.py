"""LLM prompts for executive_director_agent — bodies live in system.md / user.md."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.agents.executive_director_agent.decisions import ExecutiveAssessment
from app.agents.executive_director_agent.schemas import ExecutiveDirectorAgentRequest

RAG_EMPTY_DEFAULT = "(no relevant STO fragments)"
MEMO_EMPTY_DEFAULT = "(empty)"
TASK_HINT_EMPTY_DEFAULT = "(none)"

_PROMPTS_DIR = Path(__file__).resolve().parent

_ALLOWED_ACTIONS = frozenset(
    {
        "approve",
        "approve_registry",
        "return",
        "request_clarification",
        "await_human",
    }
)


def _extract_section(markdown: str, heading: str) -> str:
    """Take body after `## heading` until next `## ` or EOF; strip fenced code if present."""
    pattern = rf"(?ms)^## {re.escape(heading)}\s*\n+(.*?)(?=^## |\Z)"
    match = re.search(pattern, markdown)
    if not match:
        raise ValueError(f"Section not found in prompt file: {heading!r}")
    body = match.group(1).strip()
    fence = re.match(r"^```(?:\w+)?\s*\n(.*)\n```\s*$", body, flags=re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return body


@lru_cache(maxsize=1)
def _load_system_prompt() -> str:
    text = (_PROMPTS_DIR / "system.md").read_text(encoding="utf-8")
    return _extract_section(text, "SYSTEM_PROMPT (в LLM)")


@lru_cache(maxsize=1)
def _load_user_template() -> str:
    text = (_PROMPTS_DIR / "user.md").read_text(encoding="utf-8")
    return _extract_section(text, "USER_PROMPT_TEMPLATE (в LLM после подстановки)")


def get_system_prompt() -> str:
    return _load_system_prompt()


def get_user_prompt_template() -> str:
    return _load_user_template()


def __getattr__(name: str) -> Any:
    if name == "SYSTEM_PROMPT":
        return get_system_prompt()
    if name == "USER_PROMPT_TEMPLATE":
        return get_user_prompt_template()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _escape_xmlish(text: str) -> str:
    return re.sub(r"[<>]", "", text or "")


def extract_untrusted_memo(request: ExecutiveDirectorAgentRequest) -> str:
    parts: list[str] = []
    for src in (request.payload, request.human_payload):
        if not isinstance(src, dict):
            continue
        for key in ("memo", "comment", "note", "attachments_text", "user_message"):
            val = src.get(key)
            if val:
                parts.append(str(val))
        atts = src.get("attachments")
        if isinstance(atts, list):
            for item in atts:
                if isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))
                elif isinstance(item, str):
                    parts.append(item)
    return "\n".join(parts).strip()


def build_case_json(
    request: ExecutiveDirectorAgentRequest,
    assessment: ExecutiveAssessment,
) -> str:
    blob = {
        "case_id": request.case_id,
        "trigger": request.trigger,
        "registry_id": assessment.registry_id,
        "lines_count": len(assessment.lines),
        "missing_cfo": assessment.missing_cfo,
        "registry_deadline_passed": assessment.deadline_passed,
        "line_priorities": assessment.priorities,
        "risks": assessment.risks,
        "code_suggested_action": assessment.suggested_action,
        "production_need_date": (
            str(request.case_context.production_need_date)
            if request.case_context.production_need_date
            else None
        ),
    }
    return json.dumps(blob, ensure_ascii=False, default=str)


def build_task_hint(
    request: ExecutiveDirectorAgentRequest,
    assessment: ExecutiveAssessment,
) -> str:
    return (
        f"Payment registry {assessment.registry_id}: lines={len(assessment.lines)}, "
        f"missing_cfo={len(assessment.missing_cfo)}, "
        f"deadline_passed={assessment.deadline_passed}."
    )


def build_user_prompt(
    request: ExecutiveDirectorAgentRequest,
    assessment: ExecutiveAssessment,
    *,
    rag_text: str = "",
) -> str:
    rag = _escape_xmlish(rag_text).strip() or RAG_EMPTY_DEFAULT
    memo_raw = extract_untrusted_memo(request)
    memo = _escape_xmlish(memo_raw).strip() or MEMO_EMPTY_DEFAULT
    if len(memo) > 4000:
        memo = memo[:4000] + "…[truncated]"
    task_hint = build_task_hint(request, assessment) or TASK_HINT_EMPTY_DEFAULT
    return get_user_prompt_template().format(
        rag=rag,
        case_json=build_case_json(request, assessment),
        memo=memo,
        task_hint=task_hint,
    )


def build_messages(
    request: ExecutiveDirectorAgentRequest,
    assessment: ExecutiveAssessment,
    *,
    rag_text: str = "",
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": get_system_prompt()},
        {
            "role": "user",
            "content": build_user_prompt(request, assessment, rag_text=rag_text),
        },
    ]


def parse_recommendation(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        return {
            "recommendation": "Пустой ответ модели",
            "rationale": "",
            "suggested_action": "await_human",
            "needs_hitl": True,
            "norm_refs": [],
            "_parse_error": "empty",
        }
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {
                "recommendation": "Не удалось разобрать ответ модели",
                "rationale": text[:500],
                "suggested_action": "await_human",
                "needs_hitl": True,
                "norm_refs": [],
                "_parse_error": "invalid_json",
            }
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {
                "recommendation": "Не удалось разобрать ответ модели",
                "rationale": text[:500],
                "suggested_action": "await_human",
                "needs_hitl": True,
                "norm_refs": [],
                "_parse_error": "invalid_json",
            }

    action = str(data.get("suggested_action") or "await_human")
    if action == "approve_registry":
        action = "approve"
    if action not in _ALLOWED_ACTIONS:
        action = "await_human"
    norm_refs = data.get("norm_refs") or []
    if not isinstance(norm_refs, list):
        norm_refs = [str(norm_refs)]
    return {
        "recommendation": str(data.get("recommendation") or "").strip()
        or "Рекомендация не сформирована",
        "rationale": str(data.get("rationale") or "").strip(),
        "suggested_action": action,
        "needs_hitl": True,
        "norm_refs": [str(x) for x in norm_refs],
    }


async def recommend_with_llm(
    request: ExecutiveDirectorAgentRequest,
    assessment: ExecutiveAssessment,
    *,
    rag_text: str = "",
) -> dict[str, Any]:
    """Call platform LLM gateway; on failure return stub without overriding code action."""
    from app.core.config import settings
    from app.llm.gateway import llm_gateway

    if not (settings.LLM_GATEWAY_BASE_URL or "").strip():
        return {
            "recommendation": "LLM не настроен — используется детерминированная оценка кода.",
            "rationale": f"code_suggested_action={assessment.suggested_action}",
            "suggested_action": assessment.suggested_action
            if assessment.suggested_action in _ALLOWED_ACTIONS
            else "await_human",
            "needs_hitl": True,
            "norm_refs": ["СТО-28-020 §6.2", "СТО-28-020 §6.6"],
            "_route": "stub_no_gateway",
        }

    messages = build_messages(request, assessment, rag_text=rag_text)
    try:
        raw = await llm_gateway.chat(messages)
        content = (
            ((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        )
        parsed = parse_recommendation(content)
        parsed["_route"] = "gateway"
        return parsed
    except Exception as exc:  # noqa: BLE001
        return {
            "recommendation": "Модель недоступна — используется детерминированная оценка кода.",
            "rationale": f"llm_error={exc}; code_suggested_action={assessment.suggested_action}",
            "suggested_action": assessment.suggested_action
            if assessment.suggested_action in _ALLOWED_ACTIONS
            else "await_human",
            "needs_hitl": True,
            "norm_refs": ["СТО-28-020 §6.2", "СТО-28-020 §6.6"],
            "_route": "stub_error",
        }


__all__ = [
    "MEMO_EMPTY_DEFAULT",
    "RAG_EMPTY_DEFAULT",
    "SYSTEM_PROMPT",
    "TASK_HINT_EMPTY_DEFAULT",
    "USER_PROMPT_TEMPLATE",
    "build_messages",
    "build_user_prompt",
    "get_system_prompt",
    "get_user_prompt_template",
    "parse_recommendation",
    "recommend_with_llm",
]
