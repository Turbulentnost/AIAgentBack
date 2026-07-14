"""Узел 5. Гибридная маршрутизация: RuleRouter → RAG fallback → LLM.

Приоритет: детерминированные правила → при низкой уверенности / конфликте / резерве
поиск отделов в RAG (Qdrant `departments`) и выбор кандидата через LLM.
"""

from __future__ import annotations

from pathlib import Path

from agent_pochta.config import get_settings
from agent_pochta.routing import RouteEngine, route_email
from agent_pochta.routing.engine import rebuild_decision_xml
from agent_pochta.routing.recipients import build_routing_search_text
from agent_pochta.routing.models import ConfidenceLevel
from agent_pochta.routing.xml_builder import build_subject_xml_theme, sanitize_theme
from agent_pochta.schemas import Priority, ProcessingStatus, RoutingResult, SenderIdentity, SpamResult
from agent_pochta.services import ServiceContainer
from agent_pochta.routing.process_type import infer_process_type_heuristic
from agent_pochta.services.llm_analyze import resolve_partner_name
from agent_pochta.state import AgentState

_URGENT_SENDER_TYPES = {"госорган"}
_HIGH_KEYWORDS = {"претензия", "иск", "требование"}


def _determine_priority(state: AgentState, claim: bool) -> Priority:
    sender = state.get("sender")
    if sender and sender.contractor and sender.contractor.contractor_type in _URGENT_SENDER_TYPES:
        return Priority.URGENT
    if claim:
        return Priority.HIGH
    text = state.get("combined_text", "").lower()
    if any(kw in text for kw in _HIGH_KEYWORDS):
        return Priority.HIGH
    return Priority.NORMAL


def _confidence_to_float(level: ConfidenceLevel, score: int) -> float:
    return min(1.0, max(0.0, score / 100.0))


def _get_engine() -> RouteEngine:
    settings = get_settings()
    if settings.routing_rules_path:
        return RouteEngine.load(Path(settings.routing_rules_path))
    return RouteEngine.load()


def _needs_rag_fallback(decision) -> bool:
    if decision.confidence_level == ConfidenceLevel.LOW:
        return True
    if decision.has_conflict:
        return True
    if decision.match_source == "reserve":
        return True
    return False


def _rag_department_candidates(
    container: ServiceContainer,
    text: str,
    sender: SenderIdentity | None,
    *,
    recipient: str = "",
    subject: str = "",
    top_k: int = 5,
) -> list[dict]:
    search_text = build_routing_search_text(
        recipient=recipient,
        subject=subject,
        combined_text=text,
    )
    departments = container.rag.search_departments(
        search_text,
        top_k=top_k,
        recipient=recipient,
    )
    if sender and sender.allowed_departments:
        filtered = [d for d in departments if d.department_id in sender.allowed_departments]
        if filtered:
            departments = filtered
    return [
        {
            "department_id": d.department_id,
            "department_name": d.department_name,
            "head_name": d.head_name,
            "responsibility": d.responsibility,
        }
        for d in departments
    ]


def _routing_from_choice(choice: dict) -> RoutingResult:
    return RoutingResult(
        department_id=str(choice.get("department_id", "")),
        department_name=str(choice.get("department_name", "")),
        confidence=float(choice.get("confidence", 0.0)),
        reasoning=str(choice.get("reasoning", "")),
    )


def _apply_spam_decision(
    spam: SpamResult,
    settings,
    trace: list[str],
) -> dict | None:
    if not spam.is_spam:
        return None
    if spam.confidence >= settings.spam_threshold:
        return {"spam": spam, "status": ProcessingStatus.SPAM, "trace": trace}
    if spam.confidence >= settings.spam_gray_zone_low:
        return {
            "spam": spam,
            "status": ProcessingStatus.AWAITING_HUMAN,
            "human_review": True,
            "escalation_reason": (
                f"Спам в серой зоне (confidence={spam.confidence:.2f}): {spam.reason}"
            ),
            "trace": trace,
        }
    return None


def _needs_human_review(decision, settings, *, rag_used: bool, routing: RoutingResult) -> tuple[bool, str]:
    if rag_used and routing.confidence >= settings.dept_confidence_min:
        return False, ""
    if decision.confidence_level == ConfidenceLevel.LOW:
        return True, (
            f"Низкая уверенность маршрута ({decision.confidence_level.value}, "
            f"score={decision.confidence_score})"
        )
    if decision.has_conflict:
        return True, "Конфликт нескольких правил маршрутизации"
    if settings.agent_mode == "review" and decision.confidence_level == ConfidenceLevel.MEDIUM:
        return True, "Режим review: требуется подтверждение оператора"
    return False, ""


def node_route_department(state: AgentState, container: ServiceContainer) -> AgentState:
    settings = get_settings()
    trace = state.get("trace", []) + ["route_department"]
    text = state.get("combined_text", "")
    attachments_text = state.get("attachments_text", "")
    email = state["email"]
    sender = state.get("sender")
    recipient = email.routing_recipient or email.mailbox

    existing_spam = state.get("spam")
    skip_spam = existing_spam is not None and existing_spam.rule_hit == "trusted_sender"
    meta = dict(state.get("meta") or {})
    restored_from_spam = bool(meta.get("restored_from_spam"))
    if restored_from_spam:
        skip_spam = True

    engine = _get_engine()
    decision = route_email(
        email,
        combined_text=text,
        recipient=recipient,
        sender=sender,
        engine=engine,
    )

    primary = decision.services[0] if decision.services else None
    if not primary:
        primary_code = engine.rules.get("reserve_code", "00-000066")
        dept_name = engine.rules.get("reserve_name", primary_code)
    else:
        primary_code = primary.code
        dept_name = primary.name

    from agent_pochta.services.routing_departments import resolve_department_display_name

    dept_name = resolve_department_display_name(primary_code, dept_name)

    rule_routing = RoutingResult(
        department_id=primary_code,
        department_name=dept_name,
        confidence=_confidence_to_float(decision.confidence_level, decision.confidence_score),
        reasoning=primary.reasoning if primary else decision.match_source,
    )

    rag_used = False
    llm_candidates = [{"department_id": primary_code, "department_name": dept_name}]
    if settings.rag_department_enabled and _needs_rag_fallback(decision):
        rag_candidates = _rag_department_candidates(
            container,
            text,
            sender,
            recipient=recipient,
            subject=email.subject or "",
        )
        if rag_candidates:
            rag_used = True
            llm_candidates = rag_candidates
            trace = trace + ["route_department_rag"]

    routing = rule_routing
    use_llm_analyze = bool(settings.llm_gateway_url and not settings.use_stubs)
    xml_theme: str | None = None
    llm_partner: str | None = None
    resolved_process: str | None = None

    if not skip_spam and use_llm_analyze:
        analysis = container.llm.analyze_incoming(
            email,
            text,
            llm_candidates,
            sender=sender,
            skip_spam_check=skip_spam,
            attachments_text=attachments_text,
            claim=decision.claim,
        )
        spam = analysis.spam
        spam_patch = _apply_spam_decision(spam, settings, trace)
        if spam_patch:
            return spam_patch
        summary_ru = analysis.summary_ru
        xml_theme = analysis.xml_theme
        llm_partner = analysis.partner_name
        resolved_process = analysis.process_type
        if rag_used:
            routing = analysis.routing
    elif rag_used:
        spam = existing_spam
        choice = container.llm.choose_department(text, llm_candidates)
        routing = _routing_from_choice(choice)
        summary_ru = container.llm.summarize_ru(
            email,
            text,
            routing=routing,
            sender=sender,
            attachments_text=attachments_text,
        )
        xml_theme = build_subject_xml_theme(
            email.subject or "",
            combined_text=text,
            claim=decision.claim,
        )
    else:
        spam = existing_spam
        summary_ru = container.llm.summarize_ru(
            email,
            text,
            routing=rule_routing,
            sender=sender,
            attachments_text=attachments_text,
        )
        xml_theme = build_subject_xml_theme(
            email.subject or "",
            combined_text=text,
            claim=decision.claim,
        )

    if resolved_process is None:
        resolved_process = infer_process_type_heuristic(
            email.subject or "",
            text,
            claim=decision.claim,
        )

    decision = decision.model_copy(update={"process": resolved_process})
    decision = decision.model_copy(
        update={
            "services": [
                svc.model_copy(update={"process": resolved_process})
                for svc in decision.services
            ]
        }
    )

    resolved_partner = resolve_partner_name(
        llm_partner=llm_partner,
        rag_partner=decision.partner,
        email=email,
        body_text=text or email.body_text,
        summary_ru=summary_ru,
        qdrant_url=settings.qdrant_url if settings.rag_department_enabled else None,
    )
    initial_partner = decision.partner
    if resolved_partner != decision.partner:
        decision = decision.model_copy(update={"partner": resolved_partner})

    priority = _determine_priority(state, decision.claim)
    routing = routing.model_copy(update={"priority": priority})

    if xml_theme:
        decision = decision.model_copy(update={"theme": sanitize_theme(xml_theme)})

    if routing.department_id and routing.department_id != primary_code:
        decision = rebuild_decision_xml(
            email,
            decision,
            recipient=recipient,
            department_id=routing.department_id,
            department_name=routing.department_name,
            process=resolved_process,
        )
    else:
        decision = rebuild_decision_xml(
            email,
            decision,
            recipient=recipient,
            process=resolved_process,
        )

    human, reason = _needs_human_review(decision, settings, rag_used=rag_used, routing=routing)
    meta.update(
        {
            "routing_decision": {
                "organization": decision.organization,
                "direction": decision.direction,
                "confidence_level": decision.confidence_level.value,
                "confidence_score": decision.confidence_score,
                "match_source": decision.match_source,
                "has_conflict": decision.has_conflict,
                "partner": decision.partner,
                "claim": decision.claim,
            },
            "xml_document": decision.xml_document,
            "routing_recipient": recipient,
            "rag_fallback": rag_used,
        }
    )
    if rag_used:
        meta["rag_candidates"] = llm_candidates

    if human or meta.get("restored_from_spam"):
        escalation = reason
        if meta.get("restored_from_spam"):
            escalation = "Восстановлено из спама: требуется подтверждение оператора"
            trace = trace + ["restored_from_spam_hitl"]
        return {
            "spam": spam,
            "routing": routing,
            "summary_ru": summary_ru,
            "status": ProcessingStatus.AWAITING_HUMAN,
            "human_review": True,
            "escalation_reason": escalation,
            "trace": trace,
            "meta": meta,
        }

    return {
        "spam": spam,
        "routing": routing,
        "summary_ru": summary_ru,
        "trace": trace,
        "meta": meta,
    }
