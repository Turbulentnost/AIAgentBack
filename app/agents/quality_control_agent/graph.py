"""LangGraph multi-agent quality-control pipeline (parallel doc + sample rules)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from app.agents.quality_control_agent.rules_registry import (
    RULES_VERSION,
    build_mandatory_documents,
    build_sample_rule,
    evaluate_document_completeness,
    evaluate_scrap_decision,
    normalize_category,
)
from app.agents.quality_control_agent.schemas import QualityControlPayload
from app.agents.quality_control_agent.sla import build_deadlines
from app.agents.quality_control_agent.state import QualityControlState


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(*values: Any) -> float | None:
    for value in values:
        parsed = _as_float(value)
        if parsed is not None:
            return parsed
    return None


def _extract_context(state: QualityControlState) -> dict[str, Any]:
    source = dict(state.get("source_data") or {})
    nested = source.get("quality") if isinstance(source.get("quality"), dict) else {}
    fields = source.get("fields") if isinstance(source.get("fields"), dict) else {}
    role = dict(state.get("role_context") or {})
    category = (
        nested.get("item_group")
        or nested.get("category")
        or fields.get("item_group")
        or role.get("item_group")
        or source.get("item_group")
    )
    present_docs = (
        nested.get("present_docs")
        or nested.get("documents")
        or role.get("present_docs")
        or source.get("present_docs")
        or []
    )
    if isinstance(present_docs, str):
        present_docs = [present_docs]
    lot_qty = _first_number(
        nested.get("lot_qty"),
        nested.get("quantity"),
        nested.get("Количество"),
        fields.get("quantity"),
        fields.get("Количество"),
        fields.get("lot_qty"),
        role.get("lot_qty"),
        role.get("quantity"),
        source.get("quantity"),
        source.get("lot_qty"),
    )
    return {
        "category": normalize_category(str(category) if category else None),
        "present_docs": [str(d) for d in present_docs],
        "lot_qty": lot_qty,
        "scrap_pct": _as_float(nested.get("scrap_pct") or role.get("scrap_pct")),
        "analog_in_nomenclature": nested.get(
            "analog_in_nomenclature",
            role.get("analog_in_nomenclature", True),
        ),
        "presentation_ref": nested.get("presentation_ref")
        or nested.get("shipment_ref")
        or role.get("presentation_ref")
        or source.get("presentation_ref")
        or state.get("case_id"),
        "nomenclature_ref": nested.get("nomenclature_ref")
        or fields.get("nomenclature")
        or fields.get("Номенклатура")
        or source.get("nomenclature"),
        "supplier_ref": nested.get("supplier_ref")
        or nested.get("supplier")
        or source.get("supplier_ref")
        or source.get("supplier"),
        "supplier_quality_rating": nested.get("supplier_quality_rating")
        or role.get("supplier_quality_rating")
        or source.get("supplier_quality_rating"),
        "inspector_id": nested.get("inspector_id") or role.get("inspector_id"),
        "quality_stage": str(
            role.get("quality_stage") or state.get("quality_stage") or "queued"
        ),
    }


async def validate_request(state: QualityControlState) -> QualityControlState:
    if not state.get("case_id") or not state.get("correlation_id"):
        return {
            **state,
            "error": "case_id и correlation_id обязательны",
            "requires_human": False,
            "actions": ["FAILED"],
        }
    return {**state, "error": None}


async def load_context(state: QualityControlState) -> QualityControlState:
    if state.get("error"):
        return state
    ctx = _extract_context(state)
    deadlines = build_deadlines(category=ctx["category"])
    return {
        **state,
        "category": ctx["category"],
        "present_docs": ctx["present_docs"],
        "lot_qty": ctx["lot_qty"],
        "scrap_pct": ctx["scrap_pct"],
        "analog_in_nomenclature": ctx["analog_in_nomenclature"],
        "quality_stage": ctx["quality_stage"],
        "presentation": {
            "presentation_ref": ctx["presentation_ref"],
            "nomenclature_ref": ctx["nomenclature_ref"],
            "supplier_ref": ctx["supplier_ref"],
            "supplier_quality_rating": ctx["supplier_quality_rating"],
            "inspector_id": ctx["inspector_id"],
            "lot_qty": ctx["lot_qty"],
        },
        "deadlines": deadlines,
    }


async def _run_doc_rules(state: QualityControlState) -> dict[str, Any]:
    findings = evaluate_document_completeness(
        state.get("category"),
        state.get("present_docs"),
        str(state.get("case_id") or "unknown"),
    )
    docs = build_mandatory_documents(state.get("category"), state.get("present_docs"))
    return {
        "doc_findings": [f.model_dump(mode="json") for f in findings],
        "mandatory_documents": [d.model_dump(mode="json") for d in docs],
    }


async def _run_sample_rules(state: QualityControlState) -> dict[str, Any]:
    presentation = dict(state.get("presentation") or {})
    scrap = evaluate_scrap_decision(
        state.get("scrap_pct"),
        analog_in_nomenclature=state.get("analog_in_nomenclature"),
    )
    sample = build_sample_rule(
        state.get("category"),
        lot_qty=state.get("lot_qty") or presentation.get("lot_qty"),
        analog_in_nomenclature=state.get("analog_in_nomenclature"),
        presentation_ref=presentation.get("presentation_ref"),
        nomenclature_ref=presentation.get("nomenclature_ref"),
        supplier_ref=presentation.get("supplier_ref"),
        supplier_quality_rating=presentation.get("supplier_quality_rating"),
        require_second_sample=bool(scrap.get("require_second_sample")),
    )
    return {
        "sample_rule": sample.model_dump(mode="json"),
        "scrap_decision": scrap,
    }


async def parallel_rules(state: QualityControlState) -> QualityControlState:
    if state.get("error"):
        return state
    doc_result, sample_result = await asyncio.gather(
        _run_doc_rules(state),
        _run_sample_rules(state),
    )
    return {
        **state,
        **doc_result,
        **sample_result,
        "parallel_results": {
            "doc_rules": doc_result,
            "sample_rules": sample_result,
            "rules_version": RULES_VERSION,
        },
    }


def _route_role(stage: str) -> tuple[str | None, str | None, list[str], bool]:
    """Map quality stage → next_role, next_status, actions, requires_human."""
    stage = (stage or "queued").lower()
    if stage in {"queued", "quality_queued"}:
        return "otk_head_agent", "quality_queued", ["ASSIGN_ENGINEER"], True
    if stage in {"assigned", "quality_assigned"}:
        return "quality_engineer_agent", "quality_assigned", ["DOC_CHECK"], True
    if stage in {"doc_check", "quality_doc_check"}:
        return "quality_engineer_agent", "quality_doc_check", ["BUILD_PROGRAM"], True
    if stage in {"inspection", "quality_inspection"}:
        return (
            "quality_engineer_agent",
            "quality_inspection",
            ["RECORD_INSPECTION"],
            True,
        )
    if stage in {"decision", "quality_decision"}:
        return "quality_engineer_agent", "quality_decision", ["IDENTIFY_RESULT"], True
    if stage in {"nonconformity", "act_confirm"}:
        return "otk_head_agent", "nonconformity", ["CONFIRM_NC_ACT"], True
    if stage in {"zdk", "disposition", "isolated"}:
        return "quality_deputy_director_agent", "isolated", ["DRAFT_DISPOSITION"], True
    if stage in {"released", "quality_released"}:
        return None, "quality_released", ["RELEASED"], False
    return "otk_head_agent", "quality_queued", ["ASSIGN_ENGINEER"], True


async def route_by_role(state: QualityControlState) -> QualityControlState:
    if state.get("error"):
        return {**state, "actions": ["FAILED"], "requires_human": False}

    next_role, next_status, actions, requires_human = _route_role(
        str(state.get("quality_stage") or "queued")
    )
    scrap = dict(state.get("scrap_decision") or {})
    if scrap.get("require_zdk") and next_status not in {"quality_released"}:
        if str(state.get("quality_stage") or "") in {
            "decision",
            "quality_decision",
            "nonconformity",
        }:
            next_role = "quality_deputy_director_agent"
            next_status = "nonconformity"
            actions = ["HANDOFF_ZDK", *actions]
            requires_human = True

    presentation = dict(state.get("presentation") or {})
    payload = QualityControlPayload(
        presentation_ref=presentation.get("presentation_ref"),
        nomenclature_ref=presentation.get("nomenclature_ref"),
        item_group=state.get("category"),
        supplier_ref=presentation.get("supplier_ref"),
        supplier_quality_rating=presentation.get("supplier_quality_rating"),
        control_rule_ids=[
            str((state.get("sample_rule") or {}).get("rule_id") or ""),
            str(scrap.get("rule_id") or ""),
        ],
        mandatory_documents=state.get("mandatory_documents") or [],
        sample_rule=state.get("sample_rule"),
        sample_size=(state.get("sample_rule") or {}).get("sample_size"),
        inspector_id=presentation.get("inspector_id"),
        quality_status=next_status,
        disposition=scrap.get("disposition"),
        deadlines=state.get("deadlines") or {},
        findings=state.get("doc_findings") or [],
        calculated_at=datetime.now(timezone.utc),
    )
    sample_rule = dict(state.get("sample_rule") or {})
    return {
        **state,
        "next_role": next_role,
        "next_status": next_status,
        "actions": actions,
        "requires_human": requires_human,
        "sample_rule": sample_rule,
        "quality_control": payload.model_dump(mode="json"),
        "draft_artifacts": {
            "rules_version": RULES_VERSION,
            "scrap_decision": scrap,
            "control_program": sample_rule,
            "lot_qty": state.get("lot_qty") or presentation.get("lot_qty"),
            "presentation_ref": presentation.get("presentation_ref"),
        },
        "summary": (
            f"Quality pipeline: stage={state.get('quality_stage')}, "
            f"next={next_role or 'done'}, status={next_status}"
        ),
    }


async def finalize(state: QualityControlState) -> QualityControlState:
    return {
        **state,
        "data_confidence": "high" if not state.get("error") else "low",
        "requires_human_review": bool(state.get("requires_human")),
    }


def _after_validate(state: QualityControlState) -> Literal["load_context", "finalize"]:
    return "finalize" if state.get("error") else "load_context"


def build_quality_control_graph():
    graph = StateGraph(QualityControlState)
    graph.add_node("validate_request", validate_request)
    graph.add_node("load_context", load_context)
    graph.add_node("parallel_rules", parallel_rules)
    graph.add_node("route_by_role", route_by_role)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "validate_request")
    graph.add_conditional_edges(
        "validate_request",
        _after_validate,
        {"load_context": "load_context", "finalize": "finalize"},
    )
    graph.add_edge("load_context", "parallel_rules")
    graph.add_edge("parallel_rules", "route_by_role")
    graph.add_edge("route_by_role", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


quality_control_graph = build_quality_control_graph()


async def run_quality_pipeline(payload: dict[str, Any]) -> dict[str, Any]:
    initial: QualityControlState = {
        "case_id": str(payload.get("case_id") or ""),
        "correlation_id": str(payload.get("correlation_id") or ""),
        "source_data": dict(payload.get("source_data") or {}),
        "role_context": dict(payload.get("role_context") or {}),
        "quality_stage": str(
            (payload.get("role_context") or {}).get("quality_stage")
            or payload.get("quality_stage")
            or "queued"
        ),
    }
    return await quality_control_graph.ainvoke(initial)


__all__ = [
    "build_quality_control_graph",
    "quality_control_graph",
    "run_quality_pipeline",
]
