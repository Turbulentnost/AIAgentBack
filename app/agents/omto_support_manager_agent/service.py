from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.agents.omto_support_manager_agent.checks import evaluate_mandatory_fields
from app.agents.omto_support_manager_agent.schemas import (
    FIELD_LABELS_RU,
    MANDATORY_FIELD_KEYS,
    OmtoSupportManagerOutput,
)
from app.agents.procurement_role_agents.schemas import (
    ProcurementRoleAgentRequest,
    ProcurementRoleAgentResult,
)
from app.models.enums import ConfidenceLevel


def _extract_fields(source_data: dict[str, Any]) -> dict[str, Any]:
    nested = source_data.get("fields")
    if isinstance(nested, dict):
        return dict(nested)
    return {
        key: source_data.get(key)
        for key in MANDATORY_FIELD_KEYS
        if key in source_data
    }


class OmtoSupportManagerService:
    """U0 mandatory-field check for OMTO accompaniment (DATA_CHECK)."""

    async def run(self, payload: dict[str, Any], *, agent_id: str) -> ProcurementRoleAgentResult:
        try:
            request = ProcurementRoleAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="failed",
                summary="Входные данные агента ОМТО не прошли проверку.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=str(payload.get("case_id") or "unknown"),
                correlation_id=str(payload.get("correlation_id") or "unknown"),
                role_status="failed",
                output_data={"validation_errors": exc.errors()},
            )

        fields = _extract_fields(request.source_data)
        findings = evaluate_mandatory_fields(fields, request.case_id)
        checked = list(MANDATORY_FIELD_KEYS)

        if not findings:
            quality_status: str = "ok"
            actions = ["PASS"]
            clarification = None
            summary = "Обязательные поля заполнены корректно."
            role_status = "completed"
            status = "completed"
            requires_human = False
        else:
            if any(
                "не заполнен" in f.message.lower() or "не заполнена" in f.message.lower()
                for f in findings
            ):
                quality_status = "incomplete"
            else:
                quality_status = "critical"
            actions = ["DATA_CHECK"]
            lines = [
                f"- {FIELD_LABELS_RU.get(f.field, f.field)}: {f.message} ({f.rule_id})"
                for f in findings
            ]
            clarification = (
                f"Требуется DATA_CHECK / уточнение по кейсу {request.case_id}.\n\n"
                + "\n".join(lines)
            )
            summary = f"Обнаружено замечаний по данным: {len(findings)}."
            role_status = "waiting_human"
            status = "waiting_human"
            requires_human = True

        output = OmtoSupportManagerOutput(
            quality_status=quality_status,  # type: ignore[arg-type]
            findings=findings,
            checked_fields=checked,
            actions=actions,
            clarification_draft=clarification,
            summary=summary,
            calculated_at=datetime.utcnow(),
        )

        return ProcurementRoleAgentResult(
            agent_id=agent_id,
            status=status,  # type: ignore[arg-type]
            summary=summary,
            data_confidence=ConfidenceLevel.HIGH,
            requires_human_review=requires_human,
            case_id=request.case_id,
            correlation_id=request.correlation_id,
            role_status=role_status,  # type: ignore[arg-type]
            wait_reason=clarification if requires_human else None,
            output_data=output.model_dump(mode="json"),
        )


__all__ = ["OmtoSupportManagerService", "evaluate_mandatory_fields"]
