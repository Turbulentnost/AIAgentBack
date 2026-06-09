from __future__ import annotations
import uuid
from datetime import date
from typing import Any

from pydantic import Field

from app.agents.common.schemas import AgentResult, BaseAgentInput, Finding


class NDControlInput(BaseAgentInput):
    change_request_id: uuid.UUID | None = None
    reason: str | None = None
    release_date: date | None = None
    effective_date: date | None = None
    change_text: str | None = None
    assumed_document_id: uuid.UUID | None = None
    assumed_document_code: str | None = None
    attachments: list[str] = Field(default_factory=list)
    distribution_list: list[str] = Field(default_factory=list)
    initiator_comment: str | None = None
    approval_user_ids: list[uuid.UUID] = Field(default_factory=list)


class NDControlStructuredResult(AgentResult):
    change_request_id: uuid.UUID | None = None
    selected_document: dict[str, Any] | None = None
    confidence: float | None = None
    target_locations: list[dict[str, Any]] = Field(default_factory=list)
    diff: list[dict[str, Any]] = Field(default_factory=list)
    related_documents: list[dict[str, Any]] = Field(default_factory=list)
    draft_file: dict[str, Any] | None = None
    change_notice_file: dict[str, Any] | None = None
    approval_recipients: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    requires_user_review: bool = True


NDControlResult = NDControlStructuredResult
__all__ = ["NDControlInput", "NDControlResult", "NDControlStructuredResult", "AgentResult", "Finding"]
