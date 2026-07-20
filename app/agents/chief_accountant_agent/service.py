from __future__ import annotations

from pydantic import ValidationError

from app.agents.chief_accountant_agent import config
from app.agents.chief_accountant_agent.schemas import (
    ChiefAccountantAgentRequest,
    ChiefAccountantAgentResult,
)
from app.agents.common.base import BaseAgent
from app.agents.common.registry import agent_registry
from app.models.enums import ConfidenceLevel


@agent_registry.register
class ChiefAccountantAgent(BaseAgent):
    agent_id = config.CHIEF_ACCOUNTANT_AGENT_ID
    name = config.CHIEF_ACCOUNTANT_AGENT_NAME
    version = config.AGENT_VERSION
    purpose = config.CHIEF_ACCOUNTANT_AGENT_PURPOSE
    allowed_tools: list[str] = []

    async def run(self, payload: dict) -> ChiefAccountantAgentResult:
        try:
            request = ChiefAccountantAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return ChiefAccountantAgentResult(
                agent_id=self.agent_id,
                status="failed",
                summary="Входные данные агента главного бухгалтера не прошли проверку.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=str(payload.get("case_id") or "unknown"),
                correlation_id=str(payload.get("correlation_id") or "unknown"),
                role_status="failed",
                output_data={"validation_errors": exc.errors()},
            )

        wait_reason = (
            f"Правила для «{self.name}» ещё не настроены. "
            "Оркестратор удерживает кейс у этого агента."
        )
        return ChiefAccountantAgentResult(
            agent_id=self.agent_id,
            status="waiting_external",
            summary=wait_reason,
            data_confidence=ConfidenceLevel.MEDIUM,
            requires_human_review=False,
            case_id=request.case_id,
            correlation_id=request.correlation_id,
            role_status="waiting_human",
            wait_reason=wait_reason,
            output_data={},
        )


__all__ = ["ChiefAccountantAgent"]
