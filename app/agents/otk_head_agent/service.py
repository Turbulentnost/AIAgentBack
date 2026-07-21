"""OTK head role logic — assign engineer, confirm/annul NC act, handoff to ЗДК."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.agents.otk_head_agent.schemas import OtkHeadOutput
from app.agents.procurement_role_agents.schemas import (
    ProcurementRoleAgentRequest,
    ProcurementRoleAgentResult,
)
from app.agents.quality_control_agent.graph import run_quality_pipeline
from app.agents.quality_control_agent.schemas import QualityFinding
from app.agents.quality_control_agent.sla import SLA_ASSIGN_ENGINEER_WH, zdk_handoff_due_today
from app.models.enums import ConfidenceLevel


def _quality_blob(source_data: dict[str, Any], role_context: dict[str, Any]) -> dict[str, Any]:
    nested = source_data.get("quality")
    if isinstance(nested, dict):
        return dict(nested)
    return {
        key: source_data.get(key)
        for key in (
            "presentation_ref",
            "item_group",
            "category",
            "inspector_id",
            "inspector_name",
            "act_ref",
            "act_decision",
            "direction",
            "engineer_load",
        )
        if key in source_data
    } | {
        key: role_context.get(key)
        for key in (
            "quality_stage",
            "inspector_id",
            "inspector_name",
            "act_ref",
            "act_decision",
        )
        if key in role_context
    }


class OtkHeadService:
    """Local orchestrator for incoming control (H4) — MVP drafts + HITL."""

    async def run(self, payload: dict[str, Any], *, agent_id: str) -> ProcurementRoleAgentResult:
        try:
            request = ProcurementRoleAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="failed",
                summary="Входные данные агента начальника ОТК не прошли проверку.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=str(payload.get("case_id") or "unknown"),
                correlation_id=str(payload.get("correlation_id") or "unknown"),
                role_status="failed",
                output_data={"validation_errors": exc.errors()},
            )

        quality = _quality_blob(request.source_data, request.role_context)
        stage = str(
            request.role_context.get("quality_stage")
            or quality.get("quality_stage")
            or "queued"
        ).lower()

        pipeline = await run_quality_pipeline(
            {
                "case_id": request.case_id,
                "correlation_id": request.correlation_id,
                "source_data": request.source_data,
                "role_context": {**request.role_context, "quality_stage": stage},
            }
        )

        findings: list[QualityFinding] = []
        for item in pipeline.get("doc_findings") or []:
            if isinstance(item, dict):
                findings.append(QualityFinding.model_validate(item))

        now = datetime.now(timezone.utc)
        handoff_by = zdk_handoff_due_today().isoformat()

        if stage in {"nonconformity", "act_confirm"}:
            act_ref = quality.get("act_ref") or f"Ф-10-15/{request.case_id}"
            decision = str(quality.get("act_decision") or "pending").lower()
            if decision == "annul":
                output = OtkHeadOutput(
                    action="annul_nc_act",
                    act_ref=act_ref,
                    act_decision="annul",
                    next_status="quality_released",
                    next_agent=None,
                    findings=findings,
                    actions=["ANNUL_NC_ACT"],
                    summary=f"Проект аннулирования акта {act_ref} (только промышленное направление).",
                    calculated_at=now,
                    quality_control=pipeline.get("quality_control") or {},
                )
                return self._result(
                    agent_id,
                    request,
                    output,
                    status="waiting_human",
                    requires_human=True,
                    wait_reason="Требуется подпись начальника ОТК для аннулирования акта.",
                )
            if decision == "confirm":
                output = OtkHeadOutput(
                    action="handoff_zdk",
                    act_ref=act_ref,
                    act_decision="confirm",
                    handoff_zdk_by=handoff_by,
                    next_status="isolated",
                    next_agent="quality_deputy_director_agent",
                    findings=findings,
                    actions=["CONFIRM_NC_ACT", "HANDOFF_ZDK"],
                    summary=(
                        f"Проект подтверждения акта {act_ref}. "
                        f"Передача ЗДК до {handoff_by}."
                    ),
                    calculated_at=now,
                    quality_control=pipeline.get("quality_control") or {},
                )
                return self._result(
                    agent_id,
                    request,
                    output,
                    status="waiting_human",
                    requires_human=True,
                    wait_reason="Требуется подтверждение акта начальником ОТК и передача ЗДК.",
                )
            output = OtkHeadOutput(
                action="confirm_nc_act",
                act_ref=act_ref,
                act_decision="pending",
                handoff_zdk_by=handoff_by,
                next_status="nonconformity",
                next_agent="otk_head_agent",
                findings=findings,
                actions=["REVIEW_NC_ACT"],
                summary=f"Акт {act_ref} ожидает решения confirm/annul (SLA ≤ 1 раб. ч).",
                calculated_at=now,
                quality_control=pipeline.get("quality_control") or {},
            )
            return self._result(
                agent_id,
                request,
                output,
                status="waiting_human",
                requires_human=True,
                wait_reason="Требуется решение по акту несоответствия.",
            )

        engineer_id = quality.get("inspector_id") or quality.get("assigned_engineer_id")
        engineer_name = quality.get("inspector_name") or quality.get("assigned_engineer_name")
        if not engineer_id:
            findings.append(
                QualityFinding(
                    field="inspector_id",
                    rule_id="QC.OTK.ASSIGN.ENGINEER",
                    source_ref=f"case:{request.case_id}",
                    message="Не выбран инженер по качеству для назначения",
                    severity="warning",
                    suggested_fix="Указать inspector_id / inspector_name",
                )
            )
            output = OtkHeadOutput(
                action="await_presentation",
                sla_assign_wh=SLA_ASSIGN_ENGINEER_WH,
                next_status="quality_queued",
                next_agent="otk_head_agent",
                findings=findings,
                actions=["ASSIGN_ENGINEER"],
                summary=(
                    f"Предъявление в очереди. Назначить инженера в течение "
                    f"{SLA_ASSIGN_ENGINEER_WH} раб. ч."
                ),
                calculated_at=now,
                quality_control=pipeline.get("quality_control") or {},
            )
            return self._result(
                agent_id,
                request,
                output,
                status="waiting_human",
                requires_human=True,
                wait_reason="Требуется назначение инженера по качеству.",
            )

        output = OtkHeadOutput(
            action="assign_engineer",
            assigned_engineer_id=str(engineer_id),
            assigned_engineer_name=str(engineer_name) if engineer_name else None,
            sla_assign_wh=SLA_ASSIGN_ENGINEER_WH,
            next_status="quality_assigned",
            next_agent="quality_engineer_agent",
            findings=findings,
            actions=["ASSIGN_ENGINEER", "QUALITY_ASSIGNED"],
            summary=(
                f"Проект назначения инженера {engineer_name or engineer_id}. "
                f"SLA назначения ≤ {SLA_ASSIGN_ENGINEER_WH} раб. ч."
            ),
            calculated_at=now,
            quality_control=pipeline.get("quality_control") or {},
        )
        return self._result(
            agent_id,
            request,
            output,
            status="waiting_human",
            requires_human=True,
            wait_reason="Требуется подтверждение назначения инженера начальником ОТК.",
        )

    def _result(
        self,
        agent_id: str,
        request: ProcurementRoleAgentRequest,
        output: OtkHeadOutput,
        *,
        status: str,
        requires_human: bool,
        wait_reason: str | None,
    ) -> ProcurementRoleAgentResult:
        return ProcurementRoleAgentResult(
            agent_id=agent_id,
            status=status,  # type: ignore[arg-type]
            summary=output.summary,
            data_confidence=ConfidenceLevel.HIGH,
            requires_human_review=requires_human,
            case_id=request.case_id,
            correlation_id=request.correlation_id,
            role_status=status,  # type: ignore[arg-type]
            wait_reason=wait_reason,
            output_data=output.model_dump(mode="json"),
        )


__all__ = ["OtkHeadService"]
