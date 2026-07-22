"""Узлы формирования проектов, эскалации, точки HITL и инструмента записи.

Все действия — на уровне У1: агент формирует ТОЛЬКО проект. Утверждение назначения,
связывания и перераспределения выполняет начальник ОМТО через точку human-in-the-loop
(hitl.yaml). Запись в 1С — через call_tool(..., write=True, hitl_passed=...) и только
после прохождения узла human_confirm.

Открытая коллизия D01 (СТО-28-020 vs СТО-06-010): нормативная база не определяет
единственного владельца создания заказа поставщику. До закрытия D01 агент формирует
ТОЛЬКО проект назначения ответственного менеджера и НЕ создаёт заказ поставщику.
Настоящий модуль это соблюдает: writeback_1c пишет лишь реквизит «Ответственный».
"""

from __future__ import annotations

from typing import Any

from app.platform_sdk import call_tool, human_gate, llm_complete, source_ref

from .. import AGENT_ID

# Роль-подтверждающий по умолчанию (confidence.yaml → escalation.default_role).
ESCALATION_DEFAULT = "Начальник ОМТО"


def _cid(state: dict[str, Any]) -> str:
    return state.get("correlation_id", "")


def escalate_to_head(state: dict[str, Any]) -> dict[str, Any]:
    """Ф5. Проект эскалации при отсутствии кандидата или превышении срока (тип «LLM»).

    Формируется, когда матрица специализаций не покрывает группу либо нет доступного
    кандидата. Эскалация адресуется начальнику ОМТО (владельцу процесса по СТО-28-020).
    Проект назначения не создаётся; заказ поставщику не создаётся (см. коллизию D01).
    """

    llm_complete("Сформировать проект эскалации нераспределённой позиции",
                 system="escalate_to_head")
    call_tool(AGENT_ID, "notify.route", _cid(state), target="omto_head")
    findings = [{
        "type": "assignment_escalation",
        "severity": "major",
        "description": "Нет однозначной специализации/доступного кандидата — "
                       "требуется решение начальника ОМТО",
        "source": "Матрица специализаций менеджеров v3; СТО-28-020 шаг 2.1",
        "recommendation": f"Эскалировать: {ESCALATION_DEFAULT}",
    }]
    refs = [source_ref("СТО-28-020", section="шаг 2.1", clause="эскалация позиции",
                       version="07", accessed_at=state.get("_today", ""),
                       status="проверенный")]
    return {"findings": findings, "source_references": refs,
            "requires_human_review": True}


def draft_assignment(state: dict[str, Any]) -> dict[str, Any]:
    """Ф5. Проект назначения ответственного менеджера + объяснимое обоснование (тип «LLM»).

    Обоснование: специализация, загрузка, срок, приоритет. Это ПРОЕКТ (уровень У1):
    утверждение — за начальником ОМТО (узел human_confirm). Согласно открытой коллизии
    D01 агент НЕ создаёт заказ поставщику — формируется только назначение ответственного.
    """

    llm_complete("Сформировать проект назначения с объяснимым обоснованием",
                 system="draft_assignment")
    classification = state.get("classification") or {}
    target = classification.get("target_manager") or {}
    item = (state.get("queue") or [{}])[0]
    assignment_draft = {
        "position_id": item.get("position_id"),
        "manager_id": target.get("id"),
        "manager_name": target.get("name"),
        "rationale": {
            "specialization": classification.get("item_group"),
            "workload_pct": target.get("workload_pct"),
            "need_date": item.get("need_date"),
            "priority": classification.get("priority"),
        },
        "confirmed": False,          # утверждение — за начальником ОМТО
    }
    findings = [{
        "type": "assignment_proposal",
        "severity": "minor",
        "description": f"Позиция {item.get('position_id')} "
                       f"({classification.get('item_group')}, дата потребности "
                       f"{item.get('need_date')}) — менеджер {target.get('name')}: "
                       f"специализация совпадает, загрузка {target.get('workload_pct')}%",
        "source": f"Заявка на закупку {item.get('position_id')}; матрица специализаций v3; "
                  "СТО-28-020 шаг 2.1",
        "recommendation": "Подтвердить назначение",
    }]
    return {"assignment_draft": assignment_draft, "findings": findings}


def human_confirm(state: dict[str, Any]) -> dict[str, Any]:
    """Точка HITL: подтверждение назначения начальником ОМТО (interrupt).

    Агент не исполняет действие сам: возвращается решение ``pending``. Запись в 1С
    (writeback_1c) допускается только после прохождения этого узла (write_gate).
    """

    human_gate("Подтверждение назначения ответственного менеджера",
               ESCALATION_DEFAULT, correlation_id=_cid(state))
    return {"requires_human_review": True}


def writeback_1c(state: dict[str, Any]) -> dict[str, Any]:
    """Ф5. Запись ответственного менеджера в 1С:ERP после подтверждения (тип «инструмент»).

    Вызывается ТОЛЬКО после узла human_confirm: call_tool(write=True, hitl_passed=True).
    Пишется исключительно реквизит «Ответственный» по белому списку HTTP-сервиса 1С.
    Согласно открытой коллизии D01 заказ поставщику НЕ создаётся; проведение документов
    агентом не выполняется ни при каких условиях.
    """

    rec = call_tool(AGENT_ID, "erp.set_responsible_manager", _cid(state),
                    write=True, hitl_passed=True,
                    position_id=(state.get("assignment_draft") or {}).get("position_id"),
                    manager_id=(state.get("assignment_draft") or {}).get("manager_id"))
    draft = dict(state.get("assignment_draft") or {})
    draft["confirmed"] = bool(rec.ok)
    return {"assignment_draft": draft}
