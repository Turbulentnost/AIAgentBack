"""Узел 7. Создание задачи в 1С:ERP — раздел 4, узел 7 (+ ТЗ §13).

Режим dry_run (ТЗ §6): XML формируется, запись в 1С не выполняется.
"""

from __future__ import annotations

from agent_pochta.config import get_settings
from agent_pochta.schemas import ErpTaskResult, ProcessingStatus
from agent_pochta.services import ServiceContainer
from agent_pochta.state import AgentState


def node_create_erp_task(state: AgentState, container: ServiceContainer) -> AgentState:
    trace = state.get("trace", []) + ["create_erp_task"]
    settings = get_settings()
    email = state["email"]
    routing = state["routing"]
    summary = state.get("summary_ru", "")
    meta = dict(state.get("meta") or {})
    xml_document = meta.get("xml_document")

    if meta.get("skip_erp") or not routing.register_erp:
        erp = ErpTaskResult(
            success=True,
            erp_document_number="SKIP-ERP",
            erp_task_id=None,
        )
        meta["erp_skipped"] = True
        meta["erp_skip_reason"] = (
            "G.1: документ 2-й очереди / без поручения·срока·обязательства — "
            "регистрация входящей в 1С ERP не требуется"
        )
        return {"erp": erp, "trace": trace, "meta": meta}

    if settings.agent_mode == "dry_run":
        erp = ErpTaskResult(
            success=True,
            erp_document_number="DRY-RUN",
            erp_task_id=None,
        )
        meta["dry_run"] = True
        return {"erp": erp, "trace": trace, "meta": meta}

    try:
        res = container.integration.create_incoming_correspondence(
            email, routing, summary, xml_document=xml_document
        )
        erp = ErpTaskResult(
            success=True,
            erp_document_number=res["erp_document_number"],
            erp_task_id=res.get("erp_task_id") or res.get("erp_document_id"),
        )
        return {"erp": erp, "trace": trace, "meta": meta}
    except Exception as exc:  # noqa: BLE001
        erp = ErpTaskResult(success=False, error=str(exc))
        human_approved = "human_approved" in state.get("trace", [])
        escalation = (
            f"Сбой интеграции с 1С: {exc}. "
            "Запланирован повтор через Celery; уведомление администратору при исчерпании попыток."
        )
        patch: AgentState = {
            "erp": erp,
            "escalation_reason": escalation,
            "errors": state.get("errors", []) + [f"erp: {exc}"],
            "trace": trace,
            "meta": {**meta, "erp_retry_scheduled": True},
        }
        if human_approved:
            patch["human_review"] = False
        else:
            patch["status"] = ProcessingStatus.ERROR
            patch["human_review"] = True
        return patch
