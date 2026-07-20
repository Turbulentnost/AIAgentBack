"""Узел 1. Мониторинг почтового ящика (IMAP Listener) — раздел 4, узел 1.

В графе этот узел нормализует уже полученное письмо в состояние.
Реальное подключение по IMAP (порт 993, SSL/TLS, polling) и постановка
в очередь Celery/RabbitMQ — см. `agent_pochta.imap` (TODO, Фаза 2).
"""

from __future__ import annotations

from agent_pochta.schemas import ProcessingStatus
from agent_pochta.services import ServiceContainer
from agent_pochta.state import AgentState


def node_imap_listener(state: AgentState, container: ServiceContainer) -> AgentState:
    email = state["email"]
    meta = dict(state.get("meta") or {})
    meta.update({"mailbox": email.mailbox, "attachments": len(email.attachments)})
    return {
        "status": ProcessingStatus.PROCESSING,
        "human_review": False,
        "errors": [],
        "trace": ["imap_listener"],
        "meta": meta,
    }
