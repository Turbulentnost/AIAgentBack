from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.common.state import BaseAgentState
from app.agents.meeting_agent.config import HIGH_CONFIDENCE_THRESHOLD
from app.core.logging import get_logger

logger = get_logger(__name__)


class MeetingState(BaseAgentState, total=False):
    memo_ref_key: str | None
    memo_number: str | None
    meeting_type: str | None
    subject: str | None
    planned_start: str | None
    duration_minutes: int | None
    participant_fio: list[str]
    room_name: str | None
    initiator_comment: str | None
    backend: Any
    current_user: Any
    memo: dict | None
    validation_issues: list[dict]
    participants: list[dict]
    suggested_slots: list[dict]
    selected_slot: dict | None
    available_rooms: list[dict]
    selected_room: dict | None
    invite_draft: dict | None
    warnings: list[str]
    status: str
    requires_user_review: bool


async def validate_input(state: MeetingState) -> dict:
    logger.info("meeting.validate_input", memo_ref_key=state.get("memo_ref_key"))
    missing = []
    if not state.get("memo_ref_key") and not state.get("memo_number"):
        missing.append("memo_ref_key или memo_number")
    if missing:
        return {"status": "failed", "warnings": [f"Не заполнены обязательные поля: {', '.join(missing)}"]}
    return {"status": "submitted", "warnings": state.get("warnings", [])}


async def load_meeting_memo(state: MeetingState) -> dict:
    logger.info("meeting.load_meeting_memo")
    backend = state.get("backend")
    if backend is None:
        return {}
    memo = await backend.load_memo(
        memo_ref_key=state.get("memo_ref_key"),
        memo_number=state.get("memo_number"),
        current_user=state.get("current_user"),
    )
    return {"memo": _memo_to_dict(memo) if memo else None}


async def validate_memo(state: MeetingState) -> dict:
    logger.info("meeting.validate_memo")
    backend = state.get("backend")
    if backend is None:
        return {}
    issues = await backend.validate_memo(state.get("memo"), current_user=state.get("current_user"))
    issue_dicts = [_validation_issue_to_dict(item) for item in issues]
    if issue_dicts:
        return {
            "validation_issues": issue_dicts,
            "status": "requires_memo_correction",
            "requires_human_review": True,
            "summary": "Служебная записка содержит ошибки или неполные данные",
        }
    return {"validation_issues": [], "status": "memo_validated"}


async def resolve_participants(state: MeetingState) -> dict:
    logger.info("meeting.resolve_participants")
    backend = state.get("backend")
    if backend is None:
        return {}
    fio_list = state.get("participant_fio") or []
    if not fio_list and state.get("memo"):
        fio_list = (state.get("memo") or {}).get("participant_fio", [])
    participants = await backend.resolve_participants(fio_list, current_user=state.get("current_user"))
    return {"participants": [_participant_to_dict(item) for item in participants]}


async def find_meeting_slot(state: MeetingState) -> dict:
    logger.info("meeting.find_meeting_slot")
    backend = state.get("backend")
    if backend is None:
        return {}
    find_result = await backend.find_slots(
        memo=state.get("memo"),
        participants=state.get("participants", []),
        planned_start=state.get("planned_start"),
        duration_minutes=state.get("duration_minutes"),
        current_user=state.get("current_user"),
    )
    slots = find_result.slots
    slot_dicts = [_slot_to_dict(item) for item in slots]
    selected = slot_dicts[0] if slot_dicts and slot_dicts[0].get("confidence", 0) >= HIGH_CONFIDENCE_THRESHOLD else None
    return {"suggested_slots": slot_dicts, "selected_slot": selected}


async def select_meeting_room(state: MeetingState) -> dict:
    logger.info("meeting.select_meeting_room")
    backend = state.get("backend")
    if backend is None:
        return {}
    rooms = await backend.find_rooms(
        selected_slot=state.get("selected_slot"),
        room_name=state.get("room_name"),
        current_user=state.get("current_user"),
    )
    room_dicts = [_room_to_dict(item) for item in rooms]
    selected = room_dicts[0] if room_dicts else None
    return {"available_rooms": room_dicts, "selected_room": selected}


async def prepare_invite(state: MeetingState) -> dict:
    logger.info("meeting.prepare_invite")
    backend = state.get("backend")
    if backend is None:
        return {}
    invite = await backend.prepare_invite(
        memo=state.get("memo"),
        participants=state.get("participants", []),
        selected_slot=state.get("selected_slot"),
        selected_room=state.get("selected_room"),
        subject=state.get("subject"),
        current_user=state.get("current_user"),
    )
    return {"invite_draft": _invite_to_dict(invite) if invite else None}


async def save_result(state: MeetingState) -> dict:
    if state.get("status") == "requires_memo_correction":
        return {
            "status": state.get("status"),
            "requires_user_review": True,
            "summary": state.get("summary", "Требуется исправление служебной записки"),
        }
    if state.get("selected_slot") is None:
        return {
            "status": "requires_manual_slot_selection",
            "requires_user_review": True,
            "summary": "Время совещания не определено однозначно, требуется выбор пользователя",
        }
    return {
        "status": "ready_for_user_review",
        "requires_user_review": True,
        "summary": "Подготовлено приглашение на совещание, требуется подтверждение пользователя",
    }


async def wait_user_review(state: MeetingState) -> dict:
    return {"status": state.get("status", "ready_for_user_review")}


def route_after_memo_validation(state: MeetingState) -> str:
    if state.get("status") == "requires_memo_correction":
        return "save_result"
    return "resolve_participants"


NODE_SEQUENCE = [
    ("validate_input", validate_input),
    ("load_meeting_memo", load_meeting_memo),
    ("validate_memo", validate_memo),
    ("resolve_participants", resolve_participants),
    ("find_meeting_slot", find_meeting_slot),
    ("select_meeting_room", select_meeting_room),
    ("prepare_invite", prepare_invite),
    ("save_result", save_result),
    ("wait_user_review", wait_user_review),
]


def build_graph():
    graph = StateGraph(MeetingState)
    for name, fn in NODE_SEQUENCE:
        graph.add_node(name, fn)
    graph.add_edge(START, "validate_input")
    graph.add_edge("validate_input", "load_meeting_memo")
    graph.add_edge("load_meeting_memo", "validate_memo")
    graph.add_conditional_edges(
        "validate_memo",
        route_after_memo_validation,
        {
            "save_result": "save_result",
            "resolve_participants": "resolve_participants",
        },
    )
    graph.add_edge("resolve_participants", "find_meeting_slot")
    graph.add_edge("find_meeting_slot", "select_meeting_room")
    graph.add_edge("select_meeting_room", "prepare_invite")
    graph.add_edge("prepare_invite", "save_result")
    graph.add_edge("save_result", "wait_user_review")
    graph.add_edge("wait_user_review", END)
    return graph.compile()


def _memo_to_dict(item) -> dict:
    if isinstance(item, dict):
        return item
    return {
        "ref_key": getattr(item, "ref_key", None),
        "number": getattr(item, "number", None),
        "date": getattr(item, "date", None),
        "subject": getattr(item, "subject", None),
        "meeting_type": getattr(item, "meeting_type", None),
        "participant_fio": getattr(item, "participant_fio", []) or [],
        "raw": getattr(item, "raw", None),
    }


def _validation_issue_to_dict(item) -> dict:
    if isinstance(item, dict):
        return item
    return {
        "field": getattr(item, "field", None),
        "severity": getattr(item, "severity", None),
        "message": getattr(item, "message", None),
    }


def _participant_to_dict(item) -> dict:
    if isinstance(item, dict):
        return item
    return {
        "fio": getattr(item, "fio", None),
        "email": getattr(item, "email", None),
        "found": getattr(item, "found", False),
    }


def _slot_to_dict(item) -> dict:
    if isinstance(item, dict):
        return item
    return {
        "start": getattr(item, "start", None),
        "end": getattr(item, "end", None),
        "confidence": getattr(item, "confidence", 0.0),
    }


def _room_to_dict(item) -> dict:
    if isinstance(item, dict):
        return item
    return {
        "name": getattr(item, "name", None),
        "email": getattr(item, "email", None),
        "available": getattr(item, "available", None),
    }


def _invite_to_dict(item) -> dict:
    if isinstance(item, dict):
        return item
    return {
        "subject": getattr(item, "subject", None),
        "start": getattr(item, "start", None),
        "end": getattr(item, "end", None),
        "location": getattr(item, "location", None),
        "attendees": getattr(item, "attendees", []) or [],
        "body": getattr(item, "body", None),
    }
