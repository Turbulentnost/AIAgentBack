from __future__ import annotations

from typing import Any

from app.agents.builder.llm import BuilderLLMError, builder_llm
from app.agents.builder.preview_grounding import collect_preview_grounding
from app.core.logging import get_logger

logger = get_logger(__name__)


def build_partial_preview_message(grounding: dict[str, Any]) -> str:
    current = grounding.get("current_date") or {}
    date_label = current.get("date_human") or current.get("date_ru") or current.get("date_iso") or "неизвестно"
    lines = [
        f"Blueprint готов. Текущая дата: {date_label}.",
        "",
        "Чтобы получить настоящий результат работы агента (с реальными данными из браузера и баз знаний), "
        "нажмите «Запустить пробный запуск (Sandbox)» в панели по центру — агент выполнит все шаги blueprint "
        "в реальном времени и покажет трассировку, анализ и итоговый ответ.",
    ]
    return "\n".join(lines)


async def run_agent_preview(
    *,
    goal: str,
    requirements: dict[str, Any],
    blueprint: dict[str, Any] | None,
    db: Any | None = None,
    user: Any | None = None,
) -> dict[str, Any]:
    """Универсальный пробный запуск: выполнение tools из blueprint + форматирование ответа."""
    grounding = await collect_preview_grounding(
        blueprint=blueprint,
        requirements=requirements,
        goal=goal,
        db=db,
        user=user,
    )

    if not grounding.get("has_substantive_data"):
        output_text = build_partial_preview_message(grounding)
        return {
            "success": True,
            "preview_type": "partial_grounding",
            "output_text": output_text,
            "source": "preview_grounding",
            "grounding": {
                "current_date": grounding.get("current_date"),
                "tool_results": list((grounding.get("tool_results") or {}).keys()),
                "skipped_tools": grounding.get("skipped_tools") or [],
                "has_substantive_data": False,
            },
        }

    try:
        sample = await builder_llm.generate_preview_sample(
            goal=goal,
            requirements=requirements,
            blueprint=blueprint or {},
            preview_grounding=grounding,
        )
        return {
            "success": True,
            "preview_type": "grounded_simulation",
            "output_text": sample.output_text,
            "source": "preview_grounding",
            "grounding": {
                "current_date": grounding.get("current_date"),
                "tool_results": list((grounding.get("tool_results") or {}).keys()),
                "skipped_tools": grounding.get("skipped_tools") or [],
                "has_substantive_data": True,
            },
        }
    except BuilderLLMError as exc:
        return {
            "success": False,
            "preview_type": "grounded_simulation",
            "error": str(exc),
            "grounding": grounding,
        }
