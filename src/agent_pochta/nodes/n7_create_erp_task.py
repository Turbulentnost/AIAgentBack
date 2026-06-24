"""Узел 7. Создание задачи в 1С:ERP — раздел 4, узел 7 (+ раздел 5.2).

Вызов Integration Service. При сбое — повтор (10 мин × 5 попыток по ТЗ;
здесь — мгновенные ретраи tenacity, реальные задержки задаёт Celery).
Прямой доступ к 1С запрещён.
"""

from __future__ import annotations

from tenacity import retry, stop_after_attempt, wait_fixed

from agent_pochta.schemas import ErpTaskResult, ProcessingStatus
from agent_pochta.services import ServiceContainer
from agent_pochta.state import AgentState

MAX_ATTEMPTS = 5


def node_create_erp_task(state: AgentState, container: ServiceContainer) -> AgentState:
    trace = state.get("trace", []) + ["create_erp_task"]
    email = state["email"]
    routing = state["routing"]
    summary = state.get("summary_ru", "")

    @retry(stop=stop_after_attempt(MAX_ATTEMPTS), wait=wait_fixed(0), reraise=True)
    def _call() -> dict:
        return container.integration.create_incoming_correspondence(email, routing, summary)

    try:
        res = _call()
        erp = ErpTaskResult(
            success=True,
            erp_document_number=res["erp_document_number"],
            erp_task_id=res["erp_task_id"],
        )
        return {"erp": erp, "trace": trace}
    except Exception as exc:  # noqa: BLE001 — фиксируем любой сбой интеграции
        erp = ErpTaskResult(success=False, error=str(exc))
        return {
            "erp": erp,
            "status": ProcessingStatus.ERROR,
            "human_review": True,
            "escalation_reason": (
                f"Сбой интеграции с 1С после {MAX_ATTEMPTS} попыток: {exc}. "
                "Уведомление администратору; письмо остаётся в очереди."
            ),
            "errors": state.get("errors", []) + [f"erp: {exc}"],
            "trace": trace,
        }
