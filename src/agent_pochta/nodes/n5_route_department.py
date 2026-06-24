"""Узел 5. Определение профильного отдела (RAG + LLM) — раздел 4, узел 5.

Алгоритм:
 1. RAG-поиск по тексту письма → топ-3 отдела.
 2. Если узел 3 вернул допустимый список — фильтрация по нему.
 3. LLM выбирает РОВНО один отдел; возвращает confidence + reasoning.
 4. Если confidence < порога → human-in-the-loop (awaiting_human).
Признак приоритета — по типу отправителя и содержимому (СТО-34-238 п. 6.2).
"""

from __future__ import annotations

from agent_pochta.config import get_settings
from agent_pochta.schemas import Priority, ProcessingStatus, RoutingResult
from agent_pochta.services import ServiceContainer
from agent_pochta.state import AgentState

# Маркеры первой очереди (госорганы) — СТО-34-238 п. 6.2
_URGENT_SENDER_TYPES = {"госорган"}
_HIGH_KEYWORDS = {"претензия", "иск", "требование"}


def _determine_priority(state: AgentState) -> Priority:
    sender = state.get("sender")
    if sender and sender.contractor and sender.contractor.contractor_type in _URGENT_SENDER_TYPES:
        return Priority.URGENT
    text = state.get("combined_text", "").lower()
    if any(kw in text for kw in _HIGH_KEYWORDS):
        return Priority.HIGH
    return Priority.NORMAL


def node_route_department(state: AgentState, container: ServiceContainer) -> AgentState:
    settings = get_settings()
    trace = state.get("trace", []) + ["route_department"]
    text = state.get("combined_text", "")

    # 1. RAG-поиск
    candidates = container.rag.search_departments(text, top_k=3)

    # 2. Фильтрация по допустимым отделам (если узел 3 их вернул)
    sender = state.get("sender")
    allowed = sender.allowed_departments if sender else []
    if allowed:
        filtered = [d for d in candidates if d.department_id in allowed]
        candidates = filtered or candidates  # если фильтр пуст — оставляем исходные

    # 3. LLM выбирает один отдел
    choice = container.llm.choose_department(
        text,
        [{"department_id": d.department_id, "department_name": d.department_name} for d in candidates],
    )

    priority = _determine_priority(state)
    routing = RoutingResult(
        department_id=choice["department_id"],
        department_name=choice["department_name"],
        confidence=choice["confidence"],
        reasoning=choice["reasoning"],
        priority=priority,
    )

    # 4. Порог уверенности → human-in-the-loop
    if routing.confidence < settings.dept_confidence_min:
        return {
            "routing": routing,
            "status": ProcessingStatus.AWAITING_HUMAN,
            "human_review": True,
            "escalation_reason": (
                f"Низкая уверенность определения отдела ({routing.confidence:.2f} "
                f"< {settings.dept_confidence_min})"
            ),
            "trace": trace,
        }

    return {"routing": routing, "trace": trace}
