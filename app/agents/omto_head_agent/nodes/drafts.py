"""Узлы формирования проектов документов, отчётности и точки HITL.

Роль надзорная: агент НЕ исполняет закупочные операции. На уровне У1 он формирует ТОЛЬКО
проекты решений, эскалаций и отчётности. Публикация/утверждение выполняются человеком через
точку human-in-the-loop (hitl.yaml). Запись в 1С — через call_tool(..., write=True,
hitl_passed=...) и только после прохождения узла HITL (write_gate).
"""

from __future__ import annotations

from typing import Any

from app.platform_sdk import call_tool, human_gate, llm_complete

from .. import AGENT_ID


def _cid(state: dict[str, Any]) -> str:
    return state.get("correlation_id", "")


def _has_critical(state: dict[str, Any]) -> bool:
    return any(f.get("severity") == "critical" for f in state.get("findings") or [])


def draft_decision_card(state: dict[str, Any]) -> dict[str, Any]:
    """Ф5. Проект решения / корректирующего действия (тип «LLM»).

    Формируется при отсутствии критических отклонений: указывает срок и ответственного по
    major/minor-отклонениям и контроль закрытия ранее выданных корректирующих действий.
    Проект — без исполнения; подтверждает Начальник ОМТО (hitl.yaml).
    """

    llm_complete("Сформировать проект решения / корректирующего действия по отклонениям",
                 system="draft_decision_card")
    decision_card = {
        "kind": "decision_card",
        "severity_map": state.get("severity_map") or {},
        "corrective_actions": [],     # срок и ответственный — из конфигурации арендатора
        "published": False,           # публикация — после HITL
    }
    return {"decision_card": decision_card, "requires_human_review": True}


def draft_escalation(state: dict[str, Any]) -> dict[str, Any]:
    """Ф5. Проект эскалации по критическому отклонению (тип «LLM»).

    При critical — проект эскалации начальнику службы логистики и карточка критических рисков
    Операционному директору. Публикация эскалации Операционному директору — за начальником
    службы логистики (hitl.yaml). Эскалацию/карточку агент только готовит, не публикует.
    """

    llm_complete("Сформировать проект эскалации и карточку критических рисков",
                 system="draft_escalation")
    call_tool(AGENT_ID, "notify.route", _cid(state), target="logistics_head",
              case_ids=state.get("case_ids") or [])
    escalation = {
        "kind": "escalation",
        "escalate_to": "Начальник ОМТО",             # critical_role (confidence.yaml)
        "notify_default": "Начальник службы логистики",
        "risk_card_to": "Операционный директор",
        "published": False,                            # публикация — после HITL
    }
    return {"escalation": escalation, "requires_human_review": True}


def build_daily_report(state: dict[str, Any]) -> dict[str, Any]:
    """Ф6. Ежедневный отчёт, оперативная сводка и карточка рисков (тип «LLM + шаблон»).

    Формируется во ВСЕХ ветках как проект; для task_type=daily_report — основной выход.
    Все отчёты публикуются только после подтверждения человеком (публикация ежедневного
    отчёта — Начальник ОМТО, hitl.yaml).
    """

    llm_complete("Сформировать проект ежедневного отчёта, сводки и карточки рисков",
                 system="build_daily_report")
    call_tool(AGENT_ID, "docs.render_template", _cid(state), template="daily_report")
    daily_report = {
        "template": "daily_report_default",
        "task_type": state.get("task_type"),
        "period": state.get("period") or {},
        "severity_map": state.get("severity_map") or {},
        "findings_count": len(state.get("findings") or []),
        "published": False,           # публикация — после HITL
    }
    return {"daily_report": daily_report}


def human_review(state: dict[str, Any]) -> dict[str, Any]:
    """Точка human-in-the-loop: подтверждение человеком (тип «HITL / interrupt»).

    Проекты решения/эскалации/отчёта не публикуются и не фиксируются без подтверждения
    ответственной ролью. Обход узла классифицируется как неавторизованное действие
    ИИ-агента (целевое значение — 0). Реальная приостановка — через interrupt LangGraph.
    """

    approver = "Начальник ОМТО"
    action = "Подтверждение проекта решения или корректирующего действия"
    if _has_critical(state):
        action = "Публикация эскалации Операционному директору"
        approver = "Начальник службы логистики"
    human_gate(action, approver, correlation_id=_cid(state))
    return {"requires_human_review": True}
