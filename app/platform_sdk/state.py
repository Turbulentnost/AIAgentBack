"""Общие поля состояния процесса для всех ролевых агентов.

Каждый агент определяет собственный ``state.py`` (TypedDict из раздела 6.1 своего ТЗ),
расширяющий базовый набор платформенных полей: сквозной correlation_id, арендатор,
уровень уверенности, флаг участия человека, ссылки на источники, журнал ошибок и аудита.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

Confidence = Literal["high", "medium", "low"]
Severity = Literal["critical", "major", "minor"]
Status = Literal["completed", "completed_with_issues", "needs_input", "failed"]


def merge_list(existing: Any, new: Any) -> Any:
    """LangGraph-reducer, воспроизводящий слияние мини-рантайма (``_merge``).

    Если и текущее, и новое значения — списки, они конкатенируются (накопление
    findings/source_references/errors и т.п.); иначе новое значение перезаписывает
    старое. Аннотируйте им списковые поля state, чтобы поведение на боевом LangGraph
    совпадало с прежним мини-рантаймом. Мини-рантайм аннотации игнорирует.
    """

    if isinstance(existing, list) and isinstance(new, list):
        return existing + new
    return new


class BaseAgentState(TypedDict, total=False):
    """Базовые поля состояния, общие для всех агентов контура №3."""

    correlation_id: str          # сквозной идентификатор кейса (аудит, прослеживаемость)
    tenant_id: str               # арендатор / контур-владелец
    task_type: str               # тип входной задачи
    requested_by: str            # инициатор

    source_references: list[dict[str, Any]]   # документ, пункт, версия, дата обращения, статус
    findings: list[dict[str, Any]]            # выявленные наблюдения/отклонения
    data_confidence: Confidence               # рассчитывается узлом assess_confidence
    requires_human_review: bool               # true для действий из hitl.yaml на У1
    errors: list[dict[str, Any]]              # частичные отказы, недоступность источников
    audit: list[dict[str, Any]]              # трасса переходов графа с correlation_id


def make_base_state(
    correlation_id: str,
    tenant_id: str,
    *,
    task_type: str = "",
    requested_by: str = "",
) -> BaseAgentState:
    """Инициализирует базовые поля состояния значениями по умолчанию."""

    return {
        "correlation_id": correlation_id,
        "tenant_id": tenant_id,
        "task_type": task_type,
        "requested_by": requested_by,
        "source_references": [],
        "findings": [],
        "data_confidence": "high",
        "requires_human_review": False,
        "errors": [],
        "audit": [],
    }
