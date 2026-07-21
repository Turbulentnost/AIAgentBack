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
from agent_pochta.schemas import ProcessingStatus, RoutingResult, SenderIdentity, SpamResult
from agent_pochta.services import ServiceContainer
from agent_pochta.routing.dialog import DialogMode, apply_dialog_classification, classify_dialog
from agent_pochta.routing.priority import PriorityDecision, select_priority
from agent_pochta.routing.process_type import infer_process_type_heuristic
from agent_pochta.services.llm_analyze import resolve_partner_name
from agent_pochta.state import AgentState


def _determine_priority_decision(state: AgentState, claim: bool) -> PriorityDecision:
    email = state["email"]
    text = state.get("combined_text") or email.body_text or ""
    return select_priority(
        subject=email.subject or "",
        body=text,
        claim=claim,
        sender=state.get("sender"),
    )


def _apply_kind_department_hint(
    routing: RoutingResult,
    decision,
    priority_decision: PriorityDecision,
    *,
    reserve_code: str,
) -> RoutingResult:
    """Если RuleRouter ушёл в резерв — подсказать первичный отдел из G.1."""
    codes = priority_decision.primary_department_codes
    if not codes:
        return routing
    if decision.match_source != "reserve" and routing.department_id != reserve_code:
        return routing
    hint = codes[0]
    if hint == routing.department_id:
        return routing
    from agent_pochta.services.routing_departments import resolve_department_display_name

    return routing.model_copy(
        update={
            "department_id": hint,
            "department_name": resolve_department_display_name(hint, hint),
            "reasoning": (
                f"{routing.reasoning}; hint G.1 {priority_decision.document_kind}→{hint}"
            ),
        }
    )


def _confidence_to_float(level: ConfidenceLevel, score: int) -> float:
    return min(1.0, max(0.0, score / 100.0))


def _with_fallback_confidence(
    routing: RoutingResult,
    *,
    fallback: RoutingResult | None = None,
    decision_score: int = 0,
    decision_level: ConfidenceLevel = ConfidenceLevel.LOW,
) -> RoutingResult:
    """Если LLM/RAG вернул dept_confidence=0, берём score правил (иначе UI показывает 0%)."""
    if routing.confidence > 0:
        return routing
    if fallback is not None and fallback.confidence > 0:
        return routing.model_copy(update={"confidence": fallback.confidence})
    if decision_score > 0:
        return routing.model_copy(
            update={"confidence": _confidence_to_float(decision_level, decision_score)}
        )
    return routing


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
    from agent_pochta.services.routing_departments import filter_departments_for_ui_llm

    search_text = build_routing_search_text(
        recipient=recipient,
        subject=subject,
        combined_text=text,
    )
    # Берём запас: после allowlist (без директоров) список может сжаться.
    departments = container.rag.search_departments(
        search_text,
        top_k=max(top_k * 4, 20),
        recipient=recipient,
    )
    departments = filter_departments_for_ui_llm(departments)
    if sender and sender.allowed_departments:
        filtered = [d for d in departments if d.department_id in sender.allowed_departments]
        if filtered:
            departments = filtered
    return [
        {
            "department_id": d.department_id,
            "department_name": d.department_name,
            "responsibility": d.responsibility,
        }
        for d in departments[:top_k]
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
    reanalyze = bool(meta.get("reanalyze"))
    if restored_from_spam or reanalyze:
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
    need_rag = _needs_rag_fallback(decision) or reanalyze
    if settings.rag_department_enabled and need_rag:
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
    use_llm_analyze = bool(settings.llm_configured and not settings.use_stubs)
    xml_theme: str | None = None
    llm_partner: str | None = None
    resolved_process: str | None = None
    spam = existing_spam
    apply_llm_routing = rag_used or reanalyze or restored_from_spam

    if use_llm_analyze:
        analysis = container.llm.analyze_incoming(
            email,
            text,
            llm_candidates,
            sender=sender,
            skip_spam_check=skip_spam,
            attachments_text=attachments_text,
            claim=decision.claim,
        )
        if not skip_spam:
            spam = analysis.spam
            spam_patch = _apply_spam_decision(spam, settings, trace)
            if spam_patch:
                return spam_patch
        elif analysis.spam is not None:
            spam = analysis.spam
        summary_ru = analysis.summary_ru
        xml_theme = analysis.xml_theme
        llm_partner = analysis.partner_name
        resolved_process = analysis.process_type
        if apply_llm_routing:
            routing = _with_fallback_confidence(
                analysis.routing,
                fallback=rule_routing,
                decision_score=decision.confidence_score,
                decision_level=decision.confidence_level,
            )
    elif rag_used:
        spam = existing_spam
        choice = container.llm.choose_department(text, llm_candidates)
        routing = _with_fallback_confidence(
            _routing_from_choice(choice),
            fallback=rule_routing,
            decision_score=decision.confidence_score,
            decision_level=decision.confidence_level,
        )
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

    from agent_pochta.routing.onec_corrections import find_onec_correction_match
    from agent_pochta.routing.organizations import normalize_organization_code

    onec_match = find_onec_correction_match(
        recipient=recipient or "",
        sender_email=email.sender_email or "",
        subject=email.subject or "",
        body=text or email.body_text or "",
    )
    learned_partner = (onec_match or {}).get("partner") if onec_match else None
    org_from_onec = normalize_organization_code(
        (onec_match or {}).get("organization") if onec_match else None
    )
    if org_from_onec and org_from_onec != decision.organization:
        decision = decision.model_copy(update={"organization": org_from_onec})
        decision = decision.model_copy(
            update={
                "direction": engine.detect_direction(org_from_onec, decision.direction),
            }
        )

    resolved_partner = resolve_partner_name(
        llm_partner=llm_partner,
        rag_partner=decision.partner,
        email=email,
        body_text=text or email.body_text,
        summary_ru=summary_ru,
        qdrant_url=settings.qdrant_url if settings.rag_department_enabled else None,
        learned_partner=learned_partner,
    )
    initial_partner = decision.partner
    if resolved_partner != decision.partner:
        decision = decision.model_copy(update={"partner": resolved_partner})

    priority_decision = _determine_priority_decision(state, decision.claim)
    reserve_code = str(engine.rules.get("reserve_code", "00-000066"))
    routing = _apply_kind_department_hint(
        routing,
        decision,
        priority_decision,
        reserve_code=reserve_code,
    )
    routing = routing.model_copy(
        update={
            "priority": priority_decision.priority,
            "document_kind": priority_decision.document_kind,
            "queue_tier": priority_decision.queue_tier,
            "register_erp": priority_decision.register_erp,
        }
    )

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

    dialog_cls = classify_dialog(
        subject=email.subject or "",
        body=text,
        sender_email=email.sender_email or "",
        claim=decision.claim,
        process_type=resolved_process or "",
    )
    dialog_status: ProcessingStatus | None = None
    if dialog_cls.is_dialog:
        dialog_process, dialog_theme, dialog_trace = apply_dialog_classification(
            dialog_cls,
            subject=email.subject or "",
        )
        resolved_process = dialog_process
        decision = decision.model_copy(
            update={
                "match_source": dialog_trace,
                "dialog_mode": dialog_cls.mode.value if dialog_cls.mode else None,
            }
        )
        decision = rebuild_decision_xml(
            email,
            decision,
            recipient=recipient,
            theme=dialog_theme,
            process=resolved_process,
        )
        routing = routing.model_copy(
            update={
                "document_kind": dialog_cls.document_kind,
                "queue_tier": dialog_cls.queue_tier,
                "register_erp": dialog_cls.register_erp,
            }
        )
        if dialog_cls.mode == DialogMode.DORMANT:
            dialog_status = ProcessingStatus.DIALOG
        trace = trace + [dialog_trace]

    human, reason = _needs_human_review(decision, settings, rag_used=rag_used, routing=routing)

    from agent_pochta.routing.confidence import score_to_level

    meta_confidence_score = decision.confidence_score
    meta_confidence_level = decision.confidence_level
    if routing.confidence > 0:
        llm_score = round(routing.confidence * 100)
        if llm_score > meta_confidence_score:
            meta_confidence_score = llm_score
            meta_confidence_level = score_to_level(llm_score)

    meta.update(
        {
            "routing_decision": {
                "organization": decision.organization,
                "direction": decision.direction,
                "confidence_level": meta_confidence_level.value,
                "confidence_score": meta_confidence_score,
                "match_source": decision.match_source,
                "has_conflict": decision.has_conflict,
                "partner": decision.partner,
                "claim": decision.claim,
                "document_kind": routing.document_kind or priority_decision.document_kind,
                "queue_tier": routing.queue_tier,
                "register_erp": routing.register_erp,
                "has_obligation": priority_decision.has_obligation,
                "priority_reasoning": priority_decision.reasoning,
            },
            "xml_document": decision.xml_document,
            "routing_recipient": recipient,
            "rag_fallback": rag_used,
            "skip_erp": not routing.register_erp,
        }
    )
    if dialog_cls.is_dialog:
        meta["dialog"] = {
            "mode": dialog_cls.mode.value if dialog_cls.mode else None,
            "document_kind": dialog_cls.document_kind,
            "thread_markers": dialog_cls.thread_markers,
            "dormant_markers": dialog_cls.dormant_markers,
            "activation_markers": dialog_cls.activation_markers,
            "reasoning": dialog_cls.reasoning,
        }
    if rag_used:
        meta["rag_candidates"] = llm_candidates

    if human or restored_from_spam or reanalyze:
        escalation = reason
        if restored_from_spam:
            escalation = "Восстановлено из спама: требуется подтверждение оператора"
            trace = trace + ["restored_from_spam_hitl"]
        elif reanalyze:
            escalation = "Повторный анализ LLM: проверьте партнёра, отдел и организацию"
            trace = trace + ["reanalyze_hitl"]
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

    if dialog_status == ProcessingStatus.DIALOG:
        return {
            "spam": spam,
            "routing": routing,
            "summary_ru": summary_ru,
            "status": ProcessingStatus.DIALOG,
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
