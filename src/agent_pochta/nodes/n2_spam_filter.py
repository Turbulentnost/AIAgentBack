"""Узел 2. Двухуровневая спам-фильтрация — раздел 4, узел 2.

Этап 2.1 — правила (чёрные списки, стоп-слова, тех. признаки, Приложение А).
Этап 2.2 — LLM-классификатор. Серая зона [gray_low, threshold) → human-in-the-loop.
"""

from __future__ import annotations

from agent_pochta.config import get_settings
from agent_pochta.schemas import ProcessingStatus, SpamResult
from agent_pochta.services import ServiceContainer
from agent_pochta.state import AgentState

# Управляемые списки (в проде — из БД/интерфейса платформы)
BLACKLIST_DOMAINS: set[str] = set()
STOP_WORDS: set[str] = {"выгодное предложение", "только сегодня", "розыгрыш"}


def _check_rules(state: AgentState) -> SpamResult | None:
    """Этап 2.1 — проверка по правилам. None = правило не сработало."""
    email = state["email"]
    domain = email.sender_email.split("@")[-1].lower()
    if domain in BLACKLIST_DOMAINS:
        return SpamResult(is_spam=True, confidence=1.0, reason="Домен в чёрном списке",
                          rule_hit="blacklist_domain")
    text = f"{email.subject} {email.body_text}".lower()
    for word in STOP_WORDS:
        if word in text:
            return SpamResult(is_spam=True, confidence=0.99, reason=f"Стоп-слово: {word}",
                              rule_hit="stop_word")
    return None


def node_spam_filter(state: AgentState, container: ServiceContainer) -> AgentState:
    settings = get_settings()
    trace = state.get("trace", []) + ["spam_filter"]

    # Этап 2.1 — правила
    rule_result = _check_rules(state)
    if rule_result is not None:
        return {"spam": rule_result, "status": ProcessingStatus.SPAM, "trace": trace}

    # Этап 2.2 — LLM-классификатор
    result = container.llm.classify_spam(state["email"])

    if result.confidence >= settings.spam_threshold:
        return {"spam": result, "status": ProcessingStatus.SPAM, "trace": trace}

    if result.confidence >= settings.spam_gray_zone_low:
        # Серая зона → решение принимает офис-менеджер
        return {
            "spam": result,
            "status": ProcessingStatus.AWAITING_HUMAN,
            "human_review": True,
            "escalation_reason": (
                f"Спам в серой зоне (confidence={result.confidence:.2f}): {result.reason}"
            ),
            "trace": trace,
        }

    # Не спам — продолжаем обработку
    return {"spam": result, "trace": trace}
