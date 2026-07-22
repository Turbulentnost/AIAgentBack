"""Механизм human-in-the-loop (п. 4.9 ТЗ-ПЛАТФ-001).

Точки HITL реализуются через ``interrupt`` LangGraph с сохранением состояния в
checkpointer (PostgreSQL); задача возобновляется после подтверждения человеком.

Архитектура, НЕ подключено. В «сухом» режиме реальная приостановка не выполняется:
``human_gate`` только помечает, что действие требует подтверждения ответственной ролью,
и не выполняет само действие. Любое действие из hitl.yaml, выполненное без прохождения
соответствующего узла, классифицируется как неавторизованное (целевое значение — 0).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Resolution = Literal["pending", "approved", "changes_requested", "rejected"]


@dataclass
class HumanDecision:
    """Результат прохождения точки HITL."""

    action: str                      # действие из hitl.yaml
    approver_role: str               # роль-подтверждающий
    resolution: Resolution = "pending"
    passed: bool = False             # true, если действие подтверждено и может исполняться
    comment: str = ""


def human_gate(
    action: str,
    approver_role: str,
    *,
    correlation_id: str = "",
    payload: dict[str, Any] | None = None,
    autonomy_level: str = "U1",
) -> HumanDecision:
    """Точка human-in-the-loop через ``interrupt`` LangGraph.

    Боевой режим: узел вызывает ``interrupt(...)``, LangGraph сохраняет состояние в
    checkpointer и приостанавливает граф. После подтверждения человеком граф
    возобновляется (``Command(resume=<decision>)``), и ``interrupt`` возвращает
    решение — здесь оно преобразуется в :class:`HumanDecision`.

    ``resume``-значение — dict вида
    ``{"resolution": "approved|changes_requested|rejected", "passed": bool,
    "comment": str}``. Пока граф не возобновлён, управление сюда не возвращается —
    действие агентом не исполняется (целевое значение неавторизованных действий — 0).

    Резервный режим (без checkpointer, напр. dry-run/тесты): ``interrupt`` недоступен —
    возвращается ``pending``/``passed=False`` (действие не исполняется), как прежде.
    """

    try:
        from langgraph.errors import GraphInterrupt
        from langgraph.types import interrupt
    except Exception:  # noqa: BLE001 — LangGraph не установлен: прежнее поведение
        return HumanDecision(action, approver_role, resolution="pending", passed=False)

    try:
        decision = interrupt(
            {
                "kind": "hitl",
                "action": action,
                "approver_role": approver_role,
                "correlation_id": correlation_id,
                "payload": payload or {},
                "autonomy_level": autonomy_level,
            }
        )
    except GraphInterrupt:
        # Приостановка графа (нет возобновляющего значения) — пробрасываем наверх.
        raise
    except RuntimeError:
        # Вне графа/без checkpointer — действие не исполняется.
        return HumanDecision(action, approver_role, resolution="pending", passed=False)

    if isinstance(decision, dict):
        resolution = str(decision.get("resolution") or "approved")
        passed = bool(decision.get("passed", resolution == "approved"))
        comment = str(decision.get("comment") or "")
    else:
        resolution, passed, comment = "approved", True, ""
    return HumanDecision(
        action=action,
        approver_role=approver_role,
        resolution=resolution,  # type: ignore[arg-type]
        passed=passed,
        comment=comment,
    )
