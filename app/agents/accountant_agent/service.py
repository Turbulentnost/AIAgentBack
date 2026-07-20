from __future__ import annotations

from pydantic import ValidationError

from app.agents.accountant_agent import config
from app.agents.accountant_agent.decisions import (
    apply_human_action,
    assess_case,
    build_output_from_assessment,
)
from app.agents.accountant_agent.schemas import (
    AccountantAgentRequest,
    AccountantAgentResult,
)
from app.agents.common.base import BaseAgent
from app.agents.common.registry import agent_registry
from app.models.enums import ConfidenceLevel


@agent_registry.register
class AccountantAgent(BaseAgent):
    agent_id = config.ACCOUNTANT_AGENT_ID
    name = config.ACCOUNTANT_AGENT_NAME
    version = config.AGENT_VERSION
    purpose = config.ACCOUNTANT_AGENT_PURPOSE
    allowed_tools: list[str] = []

    async def run(self, payload: dict) -> AccountantAgentResult:
        try:
            request = AccountantAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return AccountantAgentResult(
                agent_id=self.agent_id,
                status="failed",
                summary="Входные данные агента бухгалтера не прошли проверку.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=str(payload.get("case_id") or "unknown"),
                correlation_id=str(payload.get("correlation_id") or "unknown"),
                role_status="failed",
                output_data={"validation_errors": exc.errors()},
            )

        if request.human_action:
            role_status, summary, output_data, next_roles = apply_human_action(request)
            return AccountantAgentResult(
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
        output_data = build_output_from_assessment(
            assessment, amount=request.case_context.amount
        )

        if assessment.kind == "data_check":
            return AccountantAgentResult(
                agent_id=self.agent_id,
                status="data_check",
                summary="Неполный case_context: нет payment_request_id.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=request.case_id,
                correlation_id=request.correlation_id,
                role_status="data_check",
                wait_reason="Требуется payment_request_id в case_context.",
                suggested_action=assessment.suggested_action,
                output_data={
                    "missing_fields": assessment.missing_fields,
                    "logs": assessment.logs,
                    "block_payment": True,
                },
            )

        if assessment.kind == "blocked":
            return AccountantAgentResult(
                agent_id=self.agent_id,
                status="blocked",
                summary="Оплата недоступна: нет полного согласования",
                data_confidence=ConfidenceLevel.HIGH,
                requires_human_review=False,
                case_id=request.case_id,
                correlation_id=request.correlation_id,
                role_status="blocked",
                output_data=output_data,
            )

        if assessment.kind == "already_paid":
            return AccountantAgentResult(
                agent_id=self.agent_id,
                status="completed",
                summary="Оплата подтверждена — уведомить контур №5",
                data_confidence=ConfidenceLevel.HIGH,
                requires_human_review=False,
                case_id=request.case_id,
                correlation_id=request.correlation_id,
                role_status="completed",
                output_data=output_data,
                next_roles_suggested=[],
            )

        wait_reasons = {
            "cancel_pending": "HITL: подтвердить отмену платежа или отложить (§6.11.6)",
            "overdue": "HITL: просрочка оплаты — mark_paid / defer / escalate",
            "queue": "HITL: отметить оплату или отложить",
        }
        summaries = {
            "cancel_pending": "Требуется подтверждение бухгалтера для отмены",
            "overdue": "Просрочка оплаты — требуется действие бухгалтера",
            "queue": "Заявка в очереди — требуется подтверждение бухгалтера",
        }
        return AccountantAgentResult(
            agent_id=self.agent_id,
            status="waiting_human",
            summary=summaries.get(assessment.kind, "Требуется действие бухгалтера"),
            data_confidence=ConfidenceLevel.HIGH,
            requires_human_review=True,
            case_id=request.case_id,
            correlation_id=request.correlation_id,
            role_status="waiting_human",
            wait_reason=wait_reasons.get(assessment.kind),
            suggested_action=assessment.suggested_action,
            output_data=output_data,
            next_roles_suggested=[],
        )


__all__ = ["AccountantAgent"]
