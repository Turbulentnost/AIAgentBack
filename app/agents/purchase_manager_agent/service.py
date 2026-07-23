from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.agents.procurement_role_agents.schemas import (
    ProcurementRoleAgentRequest,
    ProcurementRoleAgentResult,
)
from app.models.enums import ConfidenceLevel, ProcurementSourceType


class PurchaseManagerService:
    """Build a read-only manager snapshot from orchestrator reconciliation data."""

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

        snapshot = request.source_data.get("supplier_order_coverage")
        if not isinstance(snapshot, dict):
            snapshot = {}
        coverage_status = str(snapshot.get("coverage_status") or "none")
        positions = snapshot.get("positions") if isinstance(snapshot.get("positions"), list) else []
        orders = (
            snapshot.get("supplier_orders")
            if isinstance(snapshot.get("supplier_orders"), list)
            else []
        )
        summary = {
            "full": "Все позиции «К обеспечению» найдены в связанных заказах поставщику.",
            "partial": "Часть позиций уже закупается; остальные остаются на контроле.",
            "none": "Связанные заказы поставщику пока не найдены.",
        }.get(coverage_status, "Сверка заказов поставщику выполнена.")
        output = {
            "schema_version": "1.0",
            "summary": summary,
            "recommended_next_step": snapshot.get("recommended_next_step")
            or "Контролировать исполнение связанных заказов поставщику.",
            "decision_kind": "none",
            "coverage_status": coverage_status,
            "supplier_orders": orders,
            "positions": positions,
            "covered_positions": int(snapshot.get("covered_positions") or 0),
            "positions_count": int(snapshot.get("positions_count") or len(positions)),
            "checked_at": snapshot.get("checked_at"),
            "calculated_at": snapshot.get("calculated_at") or snapshot.get("checked_at"),
            "source_number": request.source_number,
            "source_1c_ref": request.source_1c_ref,
        }
        return ProcurementRoleAgentResult(
            agent_id=agent_id,
            status="waiting_external",
            summary=summary,
            data_confidence=ConfidenceLevel.HIGH,
            requires_human_review=False,
            case_id=request.case_id,
            correlation_id=request.correlation_id,
            role_status="waiting_external",
            wait_reason="Контроль исполнения связанных заказов поставщику.",
            output_data=output,
        )


__all__ = ["PurchaseManagerService"]
