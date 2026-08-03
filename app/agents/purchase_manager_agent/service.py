from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.agents.procurement_manager_agent.service import ProcurementManagerService
from app.agents.procurement_role_agents.schemas import (
    ProcurementRoleAgentRequest,
    ProcurementRoleAgentResult,
)
from app.db.session import AsyncSessionLocal
from app.models.enums import ConfidenceLevel, ProcurementSourceType


class PurchaseManagerService:
    """Delegate role-agent runs to the rich procurement manager from Jalko."""

    async def run(
        self,
        payload: dict[str, Any],
        *,
        agent_id: str,
    ) -> ProcurementRoleAgentResult:
        try:
            request = ProcurementRoleAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="failed",
                summary="Входные данные агента менеджера по закупкам не прошли проверку.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=str(payload.get("case_id") or "unknown"),
                correlation_id=str(payload.get("correlation_id") or "unknown"),
                role_status="failed",
                output_data={"validation_errors": exc.errors()},
            )
        if request.source_type is not ProcurementSourceType.PRODUCTION_MATERIAL_ORDER:
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="failed",
                summary="Агент принимает только заказы материалов в производство.",
                data_confidence=ConfidenceLevel.HIGH,
                requires_human_review=False,
                case_id=request.case_id,
                correlation_id=request.correlation_id,
                role_status="failed",
                output_data={"validation_errors": [{"field": "source_type"}]},
            )

        async with AsyncSessionLocal() as db:
            result = await ProcurementManagerService(db).run_role(payload)
            await db.commit()

        # Orchestrator maps purchase_manager + waiting_external → ORDERED.
        # Keep HITL flags from run_role while preserving purchase/order lifecycle.
        output = dict(result.output_data or {})
        snapshot = request.source_data.get("supplier_order_coverage")
        if isinstance(snapshot, dict):
            output.setdefault("coverage_status", snapshot.get("coverage_status"))
            output.setdefault("supplier_orders", snapshot.get("supplier_orders") or [])
            output.setdefault("positions", snapshot.get("positions") or [])
            output.setdefault("covered_positions", snapshot.get("covered_positions") or 0)
            output.setdefault(
                "positions_count",
                snapshot.get("positions_count") or len(snapshot.get("positions") or []),
            )
            output.setdefault("checked_at", snapshot.get("checked_at"))
        return ProcurementRoleAgentResult(
            agent_id=agent_id,
            status="waiting_external",
            summary=result.summary,
            data_confidence=result.data_confidence,
            requires_human_review=bool(result.requires_human_review),
            case_id=request.case_id,
            correlation_id=request.correlation_id,
            role_status="waiting_external",
            wait_reason=result.wait_reason
            or "Контроль исполнения и сопровождение закупки менеджером.",
            output_data=output,
        )


__all__ = ["PurchaseManagerService"]
