"""Узел 4. Обработка содержимого письма и вложений — раздел 4, узел 4.

Вложения передаются в Document Service. Извлечённый текст объединяется
с телом письма в единый контекст для узла 5.
"""

from __future__ import annotations

from agent_pochta.attachments.imap_fetch import ensure_attachments_from_imap
from agent_pochta.attachments.pipeline import process_email_attachments
from agent_pochta.services.erp_attachments import cache_email_attachment_bytes
from agent_pochta.services import ServiceContainer
from agent_pochta.state import AgentState


def node_process_content(state: AgentState, container: ServiceContainer) -> AgentState:
    email = state["email"]
    trace = state.get("trace", []) + ["process_content"]

    ensure_attachments_from_imap(email, container.vault)
    result = process_email_attachments(email, container.documents)
    cache_email_attachment_bytes(email)
    meta = dict(state.get("meta") or {})
    meta["attachments_extraction"] = result.extraction_meta

    return {
        "email": email,
        "combined_text": result.combined_text,
        "attachments_text": result.attachments_text,
        "meta": meta,
        "trace": trace,
    }
