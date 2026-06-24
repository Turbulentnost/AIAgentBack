"""Узел 8. Логирование и завершение — раздел 4, узел 8.

Сохраняет полную запись обработки письма в PostgreSQL: статусы узлов,
результаты классификации, отдел, обзор, номер документа 1С, версию агента.
Терминальный узел графа для всех веток (done / spam / awaiting_human / error).
"""

from __future__ import annotations

from agent_pochta.schemas import ProcessingStatus
from agent_pochta.services import ServiceContainer
from agent_pochta.state import AgentState


def node_finalize(state: AgentState, container: ServiceContainer) -> AgentState:
    trace = state.get("trace", []) + ["finalize"]

    # Если статус не выставлен ранее (прошли весь happy-path) — done
    status = state.get("status", ProcessingStatus.PROCESSING)
    if status == ProcessingStatus.PROCESSING:
        status = ProcessingStatus.DONE

    # TODO (Фаза 1): запись/upsert в email_messages + email_attachments через
    # agent_pochta.db. Сейчас — заглушка, чтобы граф был завершаемым без БД.
    return {"status": status, "trace": trace}
