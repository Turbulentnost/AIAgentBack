from __future__ import annotations

from pydantic import ValidationError

from app.agents.common.base import BaseAgent
from app.agents.common.registry import agent_registry
from app.agents.executive_director_agent import config
from app.agents.executive_director_agent.decisions import (
    apply_human_action,
    assess_case,
    build_awaiting_output,
)
from app.agents.executive_director_agent.prompts import recommend_with_llm
from app.agents.executive_director_agent.schemas import (
    ExecutiveDirectorAgentRequest,
    ExecutiveDirectorAgentResult,
)
from app.models.enums import ConfidenceLevel


@agent_registry.register
class ExecutiveDirectorAgent(BaseAgent):
    agent_id = config.EXECUTIVE_DIRECTOR_AGENT_ID
    name = config.EXECUTIVE_DIRECTOR_AGENT_NAME
    version = config.AGENT_VERSION
    purpose = config.EXECUTIVE_DIRECTOR_AGENT_PURPOSE
    allowed_tools: list[str] = []

    async def run(self, payload: dict) -> ExecutiveDirectorAgentResult:
        try:
            request = ExecutiveDirectorAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return ExecutiveDirectorAgentResult(
                agent_id=self.agent_id,
                status="failed",
                summary="Входные данные агента исполнительного директора не прошли проверку.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=str(payload.get("case_id") or "unknown"),
                correlation_id=str(payload.get("correlation_id") or "unknown"),
                role_status="failed",
                output_data={"validation_errors": exc.errors()},
            )

        if request.human_action:
            role_status, summary, output_data, next_roles = apply_human_action(request)
            return ExecutiveDirectorAgentResult(
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
        if assessment.missing_fields:
            return ExecutiveDirectorAgentResult(
                agent_id=self.agent_id,
                status="data_check",
                summary="Неполный case_context: нет registry_id и/или registry_lines.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=request.case_id,
                correlation_id=request.correlation_id,
                role_status="data_check",
                wait_reason=(
                    "Требуются registry_id и registry_lines в case_context "
                    "(или чтение реестра из 1С позже)."
                ),
                suggested_action=assessment.suggested_action,
                output_data={
                    "missing_fields": assessment.missing_fields,
                    "logs": assessment.logs,
                    "block_payment": True,
                },
            )

        output_data = build_awaiting_output(request.case_context, assessment)
        rag_text = str((request.payload or {}).get("rag_text") or "")
        advice = await recommend_with_llm(request, assessment, rag_text=rag_text)
        output_data["llm_recommendation"] = advice
        # Deterministic code action remains primary; LLM advice is for the human.
        return ExecutiveDirectorAgentResult(
            agent_id=self.agent_id,
            status="waiting_human",
            summary=str(
                advice.get("recommendation")
                or "Требуется резолюция ИД по реестру (≤12:00)"
            ),
            data_confidence=ConfidenceLevel.HIGH,
            requires_human_review=True,
            case_id=request.case_id,
            correlation_id=request.correlation_id,
            role_status="waiting_human",
            wait_reason="HITL: утвердить реестр / вернуть ОМТО",
            suggested_action=assessment.suggested_action,
            output_data=output_data,
            next_roles_suggested=[],
        )


__all__ = ["ExecutiveDirectorAgent"]
