from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.agents.common.schemas import AgentResult, BaseAgentInput, Finding
from app.agents.task_compliting_agent import config

CommentPresence = Literal["empty", "present"]
AssessmentStatus = Literal[
    "no_answer",
    "file_required",
    "relevant",
    "partially_relevant",
    "irrelevant",
    "unclear",
]


class TaskCompletingInput(BaseAgentInput):
    task_text: str = ""
    comment_text: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        task_text = data.get("task_text")
        if not task_text:
            task_text = data.get("task_description") or data.get("task") or ""
        comment_text = data.get("comment_text")
        if comment_text is None:
            execution_result = data.get("execution_result")
            if isinstance(execution_result, dict):
                comment_text = execution_result.get("raw", "")
            elif isinstance(execution_result, str):
                comment_text = execution_result
        data["task_text"] = str(task_text or "").strip()
        data["comment_text"] = str(comment_text or "").strip()
        return data


class TaskCompletingAssessment(BaseModel):
    comment_presence: CommentPresence
    detected_attachment_reference: bool = False
    requires_file_lookup: bool = False
    status: AssessmentStatus
    score: float | None = None
    conclusion: str
    missing_parts: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)

    @field_validator("missing_parts", "evidence", mode="before")
    @classmethod
    def coerce_str_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            return [str(value)]
        return [str(item) for item in value]

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, value: Any) -> str:
        status = str(value or config.STATUS_UNCLEAR)
        if status not in config.VALID_STATUSES:
            return config.STATUS_UNCLEAR
        return status


TaskCompletingResult = AgentResult

__all__ = [
    "AgentResult",
    "AssessmentStatus",
    "CommentPresence",
    "Finding",
    "TaskCompletingAssessment",
    "TaskCompletingInput",
    "TaskCompletingResult",
]
