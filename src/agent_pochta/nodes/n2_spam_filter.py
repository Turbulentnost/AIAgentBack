"""Узел 2. Двухуровневая спам-фильтрация — раздел 4, узел 2.

Этап 2.1 — правила (чёрные списки, стоп-слова, тех. признаки, Приложение А).
Этап 2.2 — LLM-классификатор. Серая зона [gray_low, threshold) → human-in-the-loop.
"""

from __future__ import annotations

from agent_pochta.config import get_settings
from agent_pochta.rules.ministry_not_spam import check_ministry_not_spam
from agent_pochta.rules.spam_learning import check_learned_spam_decision
from agent_pochta.rules.spam_rules import check_rule_spam
from agent_pochta.rules.spam_context import trusted_sender_pass
from agent_pochta.routing.spam_tz import check_tz_spam
from agent_pochta.schemas import ProcessingStatus
from agent_pochta.services import ServiceContainer
from agent_pochta.state import AgentState


def node_spam_filter(state: AgentState, container: ServiceContainer) -> AgentState:
    settings = get_settings()
    trace = state.get("trace", []) + ["spam_filter"]
    meta = state.get("meta") or {}

    if meta.get("restored_from_spam"):
        return {"trace": trace + ["restored_from_spam_skip"]}
    if meta.get("reanalyze"):
        return {"trace": trace + ["reanalyze_skip"]}

    ministry_pass = check_ministry_not_spam(state["email"])
    if ministry_pass is not None:
        return {"spam": ministry_pass, "trace": trace + ["ministry_not_spam"]}

    rule_result = check_rule_spam(state["email"])
    if rule_result is not None:
        return {"spam": rule_result, "status": ProcessingStatus.SPAM, "trace": trace}

    learned = check_learned_spam_decision(state["email"])
    if learned is not None:
        if learned.is_spam:
            return {
                "spam": learned.spam_result,
                "status": ProcessingStatus.SPAM,
                "trace": trace + ["spam_learned"],
            }
        trace = trace + ["spam_antipattern_pass"]

    tz_spam = check_tz_spam(
        state["email"],
        recipient=state["email"].routing_recipient or state["email"].mailbox,
    )
    if tz_spam is not None:
        return {"spam": tz_spam, "status": ProcessingStatus.SPAM, "trace": trace}

    trusted = trusted_sender_pass(state["email"], settings)
    if trusted is not None:
        return {"spam": trusted, "trace": trace}

    # LLM-спам перенесён в узел 5 (один API-вызов на письмо вместе с отделом и обзором).
    return {"trace": trace}
