"""Узел 8. Логирование и завершение — раздел 4, узел 8."""

from __future__ import annotations

import logging

from agent_pochta.config import get_settings
from agent_pochta.db.repository import persist_processing_result
from agent_pochta.routing.xml_parser import ensure_xml_document
from agent_pochta.schemas import ProcessingStatus
from agent_pochta.services import ServiceContainer
from agent_pochta.state import AgentState

logger = logging.getLogger(__name__)


def node_finalize(state: AgentState, container: ServiceContainer) -> AgentState:
    trace = state.get("trace", []) + ["finalize"]

    status = state.get("status", ProcessingStatus.PROCESSING)
    if status == ProcessingStatus.PROCESSING:
        status = ProcessingStatus.DONE

    meta = dict(state.get("meta") or {})
    if not meta.get("xml_document"):
        xml = ensure_xml_document(state)
        if xml:
            meta["xml_document"] = xml

    finalize_state: AgentState = {**state, "status": status, "trace": trace, "meta": meta}
    db_id = persist_processing_result(finalize_state)
    if db_id is not None:
        meta["db_record_id"] = str(db_id)
        _enqueue_email_vector_index(db_id)

    return {"status": status, "trace": trace, "meta": meta}


def _enqueue_email_vector_index(db_id) -> None:
    settings = get_settings()
    if not settings.email_rag_enabled or settings.rag_backend != "qdrant":
        return
    if not settings.embedding_base_url:
        return
    try:
        from agent_pochta.workers.tasks import index_email_vectors_task

        index_email_vectors_task.delay(str(db_id))
    except Exception:
        logger.exception("email_vector_index_enqueue_failed", db_record_id=str(db_id))
