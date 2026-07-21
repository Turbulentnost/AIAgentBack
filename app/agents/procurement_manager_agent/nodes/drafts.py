"""Узлы формирования проектов документов, маршрутизации и точек HITL.

Все действия — на уровне У1: агент формирует ТОЛЬКО проект. Отправка/выбор/подпись/
проведение выполняются человеком через точки human-in-the-loop (hitl.yaml). Запись в 1С —
через call_tool(..., write=True, hitl_passed=...) и только после прохождения узла HITL.
"""

from __future__ import annotations

from typing import Any

from app.platform_sdk import call_tool, human_gate, llm_complete

from .. import AGENT_ID


def _cid(state: dict[str, Any]) -> str:
    return state.get("correlation_id", "")


def route_to_kb_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Ф4. Маршрутизация к агенту КБ/ГСПП при отклонении от КД (шаг 2.5). Оркестратор.

    Согласованное отклонение должно быть получено ДО размещения заказа; до заключения
    КБ размещение блокируется (правило confidence: kd_deviation_without_kb).
    """

    call_tool(AGENT_ID, "notify.route", _cid(state), target="kb_engineer_agent")
    human_gate("Утверждение аналога или отклонения от КД",
               "Инженер КБ / ГСПП + уполномоченный руководитель",
               correlation_id=_cid(state))
    # kd_deviation остаётся None до получения заключения через оркестратор.
    return {"requires_human_review": True}


def route_to_sb_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Ф2/Ф7. Маршрутизация к агенту СБ (новый/рисковый поставщик; шаг 2.8). Оркестратор."""

    if any(s.get("is_new") for s in state.get("suppliers", []) or []):
        call_tool(AGENT_ID, "notify.route", _cid(state), target="security_officer_agent")
        return {"requires_human_review": True}
    return {}


def draft_rfq(state: dict[str, Any]) -> dict[str, Any]:
    """Ф5. Проект RFQ с КД и требованиями качества (шаг 2.6). LLM + шаблон.

    Агент НЕ выполняет внешнюю отправку. На уровне У2 — только уведомление и маршрутизация.
    """

    llm_complete("Сформировать проект RFQ по шаблону арендатора", system="draft_rfq")
    call_tool(AGENT_ID, "docs.render_template", _cid(state), template="rfq")
    rfq_draft = {"template": "rfq_default", "attached_kd": True, "sent": False}
    return {"rfq_draft": rfq_draft}


def human_send_rfq(state: dict[str, Any]) -> dict[str, Any]:
    """HITL: отправку RFQ выполняет сотрудник; агент не отправляет (interrupt)."""

    human_gate("Отправка RFQ поставщику",
               "Ведущий менеджер / менеджер по закупкам",
               correlation_id=_cid(state))
    return {"requires_human_review": True}


def draft_comparison_table(state: dict[str, Any]) -> dict[str, Any]:
    """Сравнительная таблица и рекомендуемый вариант (LLM). Выбор — за человеком."""

    llm_complete("Сформировать сравнительную таблицу и рекомендацию", system="draft_comparison_table")
    return {}


def human_select_supplier(state: dict[str, Any]) -> dict[str, Any]:
    """HITL: выбор предложения человеком (interrupt)."""

    human_gate("Выбор предложения из сравнительной таблицы",
               "Ведущий менеджер / менеджер по закупкам",
               correlation_id=_cid(state))
    return {"requires_human_review": True}


def draft_order_and_contract(state: dict[str, Any]) -> dict[str, Any]:
    """Ф7. Проекты заказа поставщику и договора (LLM + шаблон).

    Проект заказа создаётся без проведения; запись — только после HITL (write_gate).
    Подпись и согласование договора выполняют уполномоченные лица.
    """

    llm_complete("Сформировать проект заказа и договора", system="draft_order_and_contract")
    # Запись проекта заказа заблокирована до прохождения HITL (hitl_passed=False):
    call_tool(AGENT_ID, "erp.draft_purchase_order", _cid(state),
              write=True, hitl_passed=False, position_id=state.get("position_id"))
    human_gate("Размещение заказа (возникновение внешнего обязательства)",
               "Ведущий менеджер / начальник ОМТО", correlation_id=_cid(state))
    order_draft = {"posted": False}
    contract_draft = {"route": "ПЛ-34-048", "signed": False}
    return {"order_draft": order_draft, "contract_draft": contract_draft,
            "requires_human_review": True}


def link_invoice_1c(state: dict[str, Any]) -> dict[str, Any]:
    """Ф8. Связывание счёта с кейсом; даты поступления и оплаты (шаг 3.1). Инструмент."""

    call_tool(AGENT_ID, "erp.link_invoice", _cid(state),
              write=True, hitl_passed=True, position_id=state.get("position_id"))
    return {}


def draft_claim(state: dict[str, Any]) -> dict[str, Any]:
    """Ф9. Проект претензии по событию контура №5 (LLM + шаблон).

    Также используется для проекта служебной записки при отклонении цены (шаг 3.3).
    Внешняя коммуникация выполняется человеком.
    """

    llm_complete("Сформировать проект претензии / служебной записки", system="draft_claim")
    call_tool(AGENT_ID, "docs.render_template", _cid(state), template="claim")
    human_gate("Направление претензии поставщику",
               "Юрист / уполномоченный сотрудник", correlation_id=_cid(state))
    claim_draft = {"template": "claim_default", "sent": False}
    return {"claim_draft": claim_draft, "requires_human_review": True}
