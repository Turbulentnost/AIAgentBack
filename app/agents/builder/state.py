from __future__ import annotations

from typing import Any, TypedDict

from app.agents.common.state import BaseAgentState


class AgentBuilderState(BaseAgentState, total=False):
    session_id: str
    goal: str
    user_message: str | None
    current_stage: str
    status: str
    service: Any
    current_user: Any
    collected_requirements: dict
    plan_steps: list[dict]
    current_step_index: int
    blueprint: dict | None
    validation_result: dict | None
    clarifying_questions: list[str]
    assistant_messages: list[str]
    attempts: list[dict]
    requires_user_input: bool
    workflow_graph: dict | None
    conversation: list[dict[str, str]]
