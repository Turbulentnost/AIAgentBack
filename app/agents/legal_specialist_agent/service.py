from __future__ import annotations

from pydantic import ValidationError

from app.agents.common.base import BaseAgent
from app.agents.common.registry import agent_registry
from app.agents.legal_specialist_agent import config
from app.agents.legal_specialist_agent.decisions import (
    apply_human_action,
    assess_case,
    build_output_from_assessment,
)
from app.agents.legal_specialist_agent.prompts import recommend_with_llm
from app.agents.legal_specialist_agent.schemas import (
    LegalSpecialistAgentRequest,
    LegalSpecialistAgentResult,
)
from app.models.enums import ConfidenceLevel


@agent_registry.register
class LegalSpecialistAgent(BaseAgent):
    agent_id = config.LEGAL_SPECIALIST_AGENT_ID
    name = config.LEGAL_SPECIALIST_AGENT_NAME
    version = config.AGENT_VERSION
    purpose = config.LEGAL_SPECIALIST_AGENT_PURPOSE
    allowed_tools: list[str] = []

    async def run(self, payload: dict) -> LegalSpecialistAgentResult:
        try:
            request = LegalSpecialistAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return LegalSpecialistAgentResult(
                agent_id=self.agent_id,
                status="failed",
                summary="Входные данные агента юридической службы не прошли проверку.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=str(payload.get("case_id") or "unknown"),
                correlation_id=str(payload.get("correlation_id") or "unknown"),
                role_status="failed",
                output_data={"validation_errors": exc.errors()},
            )

        if request.human_action:
            role_status, summary, output_data, next_roles = apply_human_action(request)
            return LegalSpecialistAgentResult(
                agent_id=self.agent_id,
                status=role_status,
                summary=summary,
                data_confidence=ConfidenceLevel.HIGH,
                requires_human_review=role_status == "waiting_human",
                case_id=request.case_id,
                correlation_id=request.correlation_id,
                role_status=role_status,  # type: ignore[arg-type]
                suggested_action=(request.human_action or "").lower(),
                output_data=output_data,
                next_roles_suggested=next_roles,
            )

        assessment = assess_case(request)
        if assessment.kind == "data_check":
            return LegalSpecialistAgentResult(
                agent_id=self.agent_id,
                status="data_check",
                summary="Неполный case_context: нет supplier_id.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=request.case_id,
                correlation_id=request.correlation_id,
                role_status="data_check",
                wait_reason="Требуется upstream.supplier_id (или payload.supplier_id).",
                suggested_action=assessment.suggested_action,
                output_data={
                    "missing_fields": assessment.missing_fields,
                    "logs": assessment.logs,
                },
            )

        output_data = build_output_from_assessment(assessment)
        if assessment.kind == "not_required":
            return LegalSpecialistAgentResult(
                agent_id=self.agent_id,
                status="completed",
                summary="Открытых авансов нет — претензия не требуется",
                data_confidence=ConfidenceLevel.HIGH,
                requires_human_review=False,
                case_id=request.case_id,
                correlation_id=request.correlation_id,
                role_status="completed",
                output_data=output_data,
                next_roles_suggested=[],
            )

        rag_text = str((request.payload or {}).get("rag_text") or "")
        advice = await recommend_with_llm(request, assessment, rag_text=rag_text)
        output_data["llm_recommendation"] = advice
        # Deterministic code action remains primary; LLM advice is for the human.
        return LegalSpecialistAgentResult(
            agent_id=self.agent_id,
            status="waiting_human",
            summary=str(
                advice.get("recommendation")
                or "Черновик претензии готов — требуется HITL юриста"
            ),
            data_confidence=ConfidenceLevel.HIGH,
            requires_human_review=True,
            case_id=request.case_id,
            correlation_id=request.correlation_id,
            role_status="waiting_human",
            wait_reason="HITL: утвердить претензию / пакет иска / вернуть",
            suggested_action=assessment.suggested_action,
            output_data=output_data,
            next_roles_suggested=[],
        )


__all__ = ["LegalSpecialistAgent"]
