from __future__ import annotations

import json
import re
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.common.state import BaseAgentState
from app.agents.task_compliting_agent import config
from app.agents.task_compliting_agent.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from app.agents.task_compliting_agent.schemas import TaskCompletingAssessment
from app.core.logging import get_logger
from app.llm.gateway import llm_gateway

logger = get_logger(__name__)


class TaskCompletingState(BaseAgentState, total=False):
    task_text: str
    comment_text: str
    assessment: dict


def _empty_assessment() -> TaskCompletingAssessment:
    return TaskCompletingAssessment(
        comment_presence=config.COMMENT_PRESENCE_EMPTY,
        detected_attachment_reference=False,
        requires_file_lookup=False,
        status=config.STATUS_NO_ANSWER,
        score=None,
        conclusion=(
            "Комментарий или вложение отсутствует. "
            "Проверить соответствие результата поставленной задаче невозможно."
        ),
        missing_parts=[],
        evidence=[],
    )


def _extract_json_payload(content: str) -> dict[str, Any]:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    return json.loads(text)


async def prepare_input(state: TaskCompletingState) -> dict:
    task_text = str(state.get("task_text", "")).strip()
    comment_text = str(state.get("comment_text", "")).strip()
    logger.info(
        "task_compliting.prepare_input",
        task_id=state.get("task_id"),
        has_comment=bool(comment_text),
    )
    return {"task_text": task_text, "comment_text": comment_text}


async def evaluate_comment(state: TaskCompletingState) -> dict:
    task_text = str(state.get("task_text", "")).strip()
    comment_text = str(state.get("comment_text", "")).strip()

    if not comment_text:
        assessment = _empty_assessment()
        return {"assessment": assessment.model_dump()}

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_PROMPT_TEMPLATE.format(
                task_text=task_text,
                comment_text=comment_text,
            ),
        },
    ]
    try:
        response = await llm_gateway.chat(messages, model=config.DEFAULT_MODEL)
        content = str(response["choices"][0]["message"]["content"])
        payload = _extract_json_payload(content)
        assessment = TaskCompletingAssessment.model_validate(payload)
    except Exception as exc:
        logger.exception("task_compliting.evaluate_comment.failed", error=str(exc))
        assessment = TaskCompletingAssessment(
            comment_presence=config.COMMENT_PRESENCE_PRESENT,
            detected_attachment_reference=False,
            requires_file_lookup=False,
            status=config.STATUS_UNCLEAR,
            score=None,
            conclusion="Не удалось разобрать ответ модели. Требуется проверка человеком.",
            missing_parts=[],
            evidence=[],
        )

    logger.info(
        "task_compliting.evaluate_comment.done",
        task_id=state.get("task_id"),
        status=assessment.status,
    )
    return {"assessment": assessment.model_dump()}


async def form_result(state: TaskCompletingState) -> dict:
    assessment_data = state.get("assessment") or _empty_assessment().model_dump()
    assessment = TaskCompletingAssessment.model_validate(assessment_data)
    requires_human_review = assessment.status in {
        config.STATUS_NO_ANSWER,
        config.STATUS_FILE_REQUIRED,
        config.STATUS_UNCLEAR,
        config.STATUS_PARTIALLY_RELEVANT,
        config.STATUS_IRRELEVANT,
    }
    data_confidence = "high" if assessment.status == config.STATUS_RELEVANT else "medium"
    if assessment.status in {config.STATUS_UNCLEAR, config.STATUS_NO_ANSWER, config.STATUS_FILE_REQUIRED}:
        data_confidence = "low"

    return {
        "summary": assessment.conclusion,
        "data_confidence": data_confidence,
        "requires_human_review": requires_human_review,
        "findings": [
            {
                "type": "task_comment_assessment",
                "severity": "medium",
                "description": assessment.conclusion,
                "source": assessment.status,
                "recommendation": None,
            }
        ],
    }


NODE_SEQUENCE = [
    ("prepare_input", prepare_input),
    ("evaluate_comment", evaluate_comment),
    ("form_result", form_result),
]


def build_graph():
    graph = StateGraph(TaskCompletingState)
    for name, fn in NODE_SEQUENCE:
        graph.add_node(name, fn)
    graph.add_edge(START, NODE_SEQUENCE[0][0])
    for (current, _), (nxt, _) in zip(NODE_SEQUENCE, NODE_SEQUENCE[1:]):
        graph.add_edge(current, nxt)
    graph.add_edge(NODE_SEQUENCE[-1][0], END)
    return graph.compile()
