"""Узел 3. Идентификация отправителя через RAG — раздел 4, узел 3.

Точный поиск email в коллекции contractors. Сценарии:
 • найден, один отдел   → отдел зафиксирован;
 • найден, несколько    → список передаётся в узел 5 для выбора по содержимому;
 • не найден            → черновик «Новый контрагент», отдел только по содержимому.
"""

from __future__ import annotations

from agent_pochta.schemas import SenderIdentity
from agent_pochta.services import ServiceContainer
from agent_pochta.state import AgentState


def node_identify_sender(state: AgentState, container: ServiceContainer) -> AgentState:
    email = state["email"]
    trace = state.get("trace", []) + ["identify_sender"]

    contractor = container.rag.find_contractor_by_email(email.sender_email)

    if contractor is None:
        sender = SenderIdentity(found=False, is_new_contractor=True, allowed_departments=[])
    else:
        sender = SenderIdentity(
            found=True,
            contractor=contractor,
            is_new_contractor=False,
            allowed_departments=contractor.department_codes,
        )

    return {"sender": sender, "trace": trace}
