from __future__ import annotations
from typing import TypedDict
class BaseAgentState(TypedDict, total=False):
    task_id: str
    document_ids: list[str]
    user_id: str | None
    documents: list[dict]
    findings: list[dict]
    summary: str
    data_confidence: str
    requires_human_review: bool
    error: str | None
