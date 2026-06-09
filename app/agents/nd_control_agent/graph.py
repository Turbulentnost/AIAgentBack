from __future__ import annotations
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.common.state import BaseAgentState
from app.agents.nd_control_agent.config import HIGH_CONFIDENCE_THRESHOLD, MEDIUM_CONFIDENCE_THRESHOLD
from app.core.logging import get_logger

logger = get_logger(__name__)

class NDControlState(BaseAgentState, total=False):
    change_request_id: str
    change_text: str
    reason: str
    service: Any
    current_user: Any
    candidates: list[dict]
    selected_document: dict | None
    confidence: float
    target_locations: list[dict]
    selected_location: dict | None
    operation: dict | None
    diff: list[dict]
    related_documents: list[dict]
    draft_file: dict | None
    change_notice_file: dict | None
    approval_recipients: list[dict]
    warnings: list[str]
    actions: list[dict]
    status: str
    requires_user_review: bool


async def validate_input(state: NDControlState) -> dict:
    logger.info("nd_control.validate_input", change_request_id=state.get("change_request_id"))
    missing = []
    if not state.get("reason"):
        missing.append("reason")
    if not state.get("change_text"):
        missing.append("change_text")
    if missing:
        return {"status": "failed", "warnings": [f"Не заполнены обязательные поля: {', '.join(missing)}"]}
    return {"status": "submitted", "warnings": state.get("warnings", [])}


async def detect_target_document(state: NDControlState) -> dict:
    logger.info("nd_control.detect_target_document")
    service = state.get("service")
    if service is None:
        return {}
    candidates = await service.detect_document(state["change_request_id"], current_user=state["current_user"])
    return {"candidates": [_candidate_to_dict(item) for item in candidates]}


async def calculate_document_confidence(state: NDControlState) -> dict:
    logger.info("nd_control.calculate_document_confidence")
    candidates = state.get("candidates", [])
    confidence = candidates[0]["score"] if candidates else 0.0
    selected = candidates[0] if confidence >= HIGH_CONFIDENCE_THRESHOLD else None
    return {"confidence": confidence, "selected_document": selected}


async def require_user_document_selection(state: NDControlState) -> dict:
    confidence = state.get("confidence", 0.0)
    status = "requires_manual_document_selection"
    if confidence >= MEDIUM_CONFIDENCE_THRESHOLD:
        return {
            "status": status,
            "requires_human_review": True,
            "summary": "Найдено несколько возможных документов, требуется выбор пользователя",
        }
    return {
        "status": status,
        "requires_human_review": True,
        "summary": "Документ не определён уверенно, требуется ручной выбор из базы знаний",
    }


async def locate_change_place(state: NDControlState) -> dict:
    logger.info("nd_control.locate_change_place")
    service = state.get("service")
    if service is None:
        return {}
    locations = await service.find_location(state["change_request_id"], current_user=state["current_user"])
    return {"target_locations": [_location_to_dict(item) for item in locations]}


async def classify_change_operation(state: NDControlState) -> dict:
    locations = state.get("target_locations", [])
    high = [item for item in locations if item.get("confidence", 0) >= 0.8]
    if len(high) != 1:
        return {
            "status": "requires_manual_location_selection",
            "requires_human_review": True,
            "summary": "Место изменения не найдено однозначно",
        }
    return {"selected_location": high[0], "operation": {"requires_manual_review": False}}


async def extract_current_text(state: NDControlState) -> dict:
    location = state.get("selected_location") or {}
    return {"current_text": location.get("current_text")}


async def apply_change_to_draft(state: NDControlState) -> dict:
    service = state.get("service")
    if service is None:
        return {}
    request = await service.apply_changes(
        state["change_request_id"],
        current_user=state["current_user"],
        location_id=(state.get("selected_location") or {}).get("id"),
    )
    full = await service.get_full(request.id)
    draft = next((item for item in full.draft_files if item.file_type == "draft"), None)
    notice = next((item for item in full.draft_files if item.file_type == "notice"), None)
    operation = full.operations[-1] if full.operations else None
    result = full.results[-1] if full.results else None
    return {
        "status": full.status.value,
        "diff": operation.diff if operation else [],
        "draft_file": _draft_file_to_dict(draft),
        "change_notice_file": _draft_file_to_dict(notice),
        "warnings": result.warnings if result and result.warnings else state.get("warnings", []),
        "actions": result.actions if result and result.actions else [],
        "related_documents": (result.metadata_ or {}).get("related_documents", []) if result else [],
    }


async def generate_diff(state: NDControlState) -> dict:
    return {"diff": state.get("diff", [])}


async def analyze_related_documents(state: NDControlState) -> dict:
    return {"related_documents": state.get("related_documents", [])}


async def generate_change_notice(state: NDControlState) -> dict:
    return {"change_notice_file": state.get("change_notice_file")}


async def prepare_approval_route(state: NDControlState) -> dict:
    return {"approval_recipients": state.get("approval_recipients", [])}


async def save_result(state: NDControlState) -> dict:
    return {
        "status": "ready_for_user_review",
        "requires_user_review": True,
        "summary": "Сформированы проект новой редакции, извещение и diff",
    }


async def wait_user_review(state: NDControlState) -> dict:
    return {"status": state.get("status", "ready_for_user_review")}


async def send_to_approval(state: NDControlState) -> dict:
    return {"status": state.get("status", "ready_for_user_review")}


def route_after_confidence(state: NDControlState) -> str:
    confidence = state.get("confidence", 0.0)
    if confidence >= HIGH_CONFIDENCE_THRESHOLD:
        return "locate_change_place"
    return "require_user_document_selection"


def route_after_location(state: NDControlState) -> str:
    if state.get("status") == "requires_manual_location_selection":
        return "save_result"
    return "extract_current_text"

NODE_SEQUENCE = [
    ("validate_input", validate_input),
    ("detect_target_document", detect_target_document),
    ("calculate_document_confidence", calculate_document_confidence),
    ("require_user_document_selection", require_user_document_selection),
    ("locate_change_place", locate_change_place),
    ("classify_change_operation", classify_change_operation),
    ("extract_current_text", extract_current_text),
    ("apply_change_to_draft", apply_change_to_draft),
    ("generate_diff", generate_diff),
    ("analyze_related_documents", analyze_related_documents),
    ("generate_change_notice", generate_change_notice),
    ("prepare_approval_route", prepare_approval_route),
    ("save_result", save_result),
    ("wait_user_review", wait_user_review),
    ("send_to_approval", send_to_approval),
]

def build_graph():
    graph = StateGraph(NDControlState)
    for name, fn in NODE_SEQUENCE:
        graph.add_node(name, fn)
    graph.add_edge(START, "validate_input")
    graph.add_edge("validate_input", "detect_target_document")
    graph.add_edge("detect_target_document", "calculate_document_confidence")
    graph.add_conditional_edges(
        "calculate_document_confidence",
        route_after_confidence,
        {
            "locate_change_place": "locate_change_place",
            "require_user_document_selection": "require_user_document_selection",
        },
    )
    graph.add_edge("require_user_document_selection", END)
    graph.add_edge("locate_change_place", "classify_change_operation")
    graph.add_conditional_edges(
        "classify_change_operation",
        route_after_location,
        {
            "save_result": "save_result",
            "extract_current_text": "extract_current_text",
        },
    )
    graph.add_edge("extract_current_text", "apply_change_to_draft")
    graph.add_edge("apply_change_to_draft", "generate_diff")
    graph.add_edge("generate_diff", "analyze_related_documents")
    graph.add_edge("analyze_related_documents", "generate_change_notice")
    graph.add_edge("generate_change_notice", "prepare_approval_route")
    graph.add_edge("prepare_approval_route", "save_result")
    graph.add_edge("save_result", "wait_user_review")
    graph.add_edge("wait_user_review", END)
    return graph.compile()


def _candidate_to_dict(item) -> dict:
    return {
        "id": item.id,
        "document_id": item.document_id,
        "document_version_id": item.document_version_id,
        "score": item.score,
        "rank": item.rank,
        "match_reason": item.match_reason,
        "matched_fragments": item.matched_fragments or [],
    }


def _location_to_dict(item) -> dict:
    return {
        "id": item.id,
        "document_id": item.document_id,
        "document_version_id": item.document_version_id,
        "section_number": item.section_number,
        "section_title": item.section_title,
        "page_number": item.page_number,
        "chunk_id": item.chunk_id,
        "location_type": item.location_type.value,
        "current_text": item.current_text,
        "confidence": item.confidence,
        "status": item.status.value,
    }


def _draft_file_to_dict(item) -> dict | None:
    if item is None:
        return None
    return {"bucket": item.draft_bucket, "object_name": item.draft_object_name, "filename": item.generated_filename}
