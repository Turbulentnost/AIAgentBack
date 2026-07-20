from __future__ import annotations

from pydantic import ValidationError

from app.agents.cfo_head_agent import config
from app.agents.cfo_head_agent.decisions import (
    apply_human_action,
    assess_case,
    build_awaiting_output,
)
from app.agents.cfo_head_agent.prompts import recommend_with_llm
from app.agents.cfo_head_agent.schemas import CfoHeadAgentRequest, CfoHeadAgentResult
from app.agents.common.base import BaseAgent
from app.agents.common.registry import agent_registry
from app.models.enums import ConfidenceLevel


@agent_registry.register
class CfoHeadAgent(BaseAgent):
    agent_id = config.CFO_HEAD_AGENT_ID
    name = config.CFO_HEAD_AGENT_NAME
    version = config.AGENT_VERSION
    purpose = config.CFO_HEAD_AGENT_PURPOSE
    allowed_tools: list[str] = []

    async def run(self, payload: dict) -> CfoHeadAgentResult:
        try:
            request = CfoHeadAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return CfoHeadAgentResult(
                agent_id=self.agent_id,
                status="failed",
                summary="Входные данные агента руководителя ЦФО не прошли проверку.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=str(payload.get("case_id") or "unknown"),
                correlation_id=str(payload.get("correlation_id") or "unknown"),
                role_status="failed",
                output_data={"validation_errors": exc.errors()},
            )

        if request.human_action:
            role_status, summary, output_data, next_roles = apply_human_action(request)
            return CfoHeadAgentResult(
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
            return CfoHeadAgentResult(
                agent_id=self.agent_id,
                status="data_check",
                summary="Неполный case_context: нет amount и/или ds_limit.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=request.case_id,
                correlation_id=request.correlation_id,
                role_status="data_check",
                wait_reason="Требуются amount и ds_limit в case_context (или чтение из 1С позже).",
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
        return CfoHeadAgentResult(
            agent_id=self.agent_id,
            status="waiting_human",
            summary=str(advice.get("recommendation") or "Требуется решение руководителя ЦФО"),
            data_confidence=ConfidenceLevel.HIGH,
            requires_human_review=True,
            case_id=request.case_id,
            correlation_id=request.correlation_id,
            role_status="waiting_human",
            wait_reason="HITL: утвердить / вернуть / отклонить заявку ЦФО",
            suggested_action=assessment.suggested_action,
            output_data=output_data,
            next_roles_suggested=[],
        )


__all__ = ["CfoHeadAgent"]
