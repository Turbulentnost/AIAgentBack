from __future__ import annotations

from pydantic import Field

from app.agents.common.schemas import AgentResult, BaseAgentInput


class DocumentAnalysisInput(BaseAgentInput):
    file_names: list[str] = Field(default_factory=list)


class DocumentAnalysisResult(AgentResult):
    analyzed_files: list[str] = Field(default_factory=list)
    lm_studio_status: str = "pending"
    warnings: list[str] = Field(default_factory=list)


__all__ = ["DocumentAnalysisInput", "DocumentAnalysisResult"]
