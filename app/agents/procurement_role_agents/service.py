from __future__ import annotations

from pydantic import ValidationError

from app.agents.common.base import BaseAgent
from app.agents.common.registry import agent_registry
from app.agents.procurement_role_agents import config
from app.agents.procurement_role_agents.schemas import (
    ProcurementRoleAgentRequest,
    ProcurementRoleAgentResult,
)
from app.agents.omto_support_manager_agent.service import OmtoSupportManagerService
from app.agents.production_preparation_engineer_agent.service import (
    ProductionPreparationEngineerService,
)
from app.models.enums import ConfidenceLevel


class _WaitingProcurementRoleAgent(BaseAgent):
    version = config.AGENT_VERSION
    allowed_tools: list[str] = []

    async def run(self, payload: dict) -> ProcurementRoleAgentResult:
        try:
            request = ProcurementRoleAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return ProcurementRoleAgentResult(
                agent_id=self.agent_id,
                status="failed",
                summary="Входные данные ролевого агента не прошли проверку.",
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
        return ProcurementRoleAgentResult(
            agent_id=self.agent_id,
            status="waiting_external",
            summary=wait_reason,
            data_confidence=ConfidenceLevel.MEDIUM,
            requires_human_review=False,
            case_id=request.case_id,
            correlation_id=request.correlation_id,
            role_status="waiting_external",
            wait_reason=wait_reason,
            output_data={},
        )


@agent_registry.register
class ProductionDispatcherAgent(_WaitingProcurementRoleAgent):
    agent_id = config.PRODUCTION_DISPATCHER_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = "Обработка закупочного кейса по сигналу точки заказа."


@agent_registry.register
class ProductionPreparationEngineerAgent(_WaitingProcurementRoleAgent):
    agent_id = config.PRODUCTION_PREPARATION_ENGINEER_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = "Обработка заказа материалов в производство."
    allowed_tools = [
        "onec_get_active_resource_specifications",
        "onec_get_free_stock",
        "onec_get_reservations",
        "onec_get_store_room_stock",
        "onec_get_open_supplier_orders",
        "onec_get_goods_in_transit",
        "onec_get_internal_transfers",
        "onec_get_available_semifinished_goods",
        "onec_get_work_in_progress",
        "onec_get_quality_stock",
    ]

    async def run(self, payload: dict) -> ProcurementRoleAgentResult:
        return await ProductionPreparationEngineerService().run(
            payload,
            agent_id=self.agent_id,
        )


@agent_registry.register
class DepartmentInitiatorAgent(_WaitingProcurementRoleAgent):
    agent_id = config.DEPARTMENT_INITIATOR_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = "Обработка заказа на внутреннее потребление."


@agent_registry.register
class WarehouseManagerAgent(_WaitingProcurementRoleAgent):
    agent_id = config.WAREHOUSE_MANAGER_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = "Обработка заказа на перемещение."


@agent_registry.register
class OmtoSupportManagerAgent(_WaitingProcurementRoleAgent):
    agent_id = config.OMTO_SUPPORT_MANAGER_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = (
        "Контроль обязательных полей и сопровождение поставки (DATA_CHECK / уточнение)."
    )

    async def run(self, payload: dict) -> ProcurementRoleAgentResult:
        return await OmtoSupportManagerService().run(
            payload,
            agent_id=self.agent_id,
        )


__all__ = [
    "DepartmentInitiatorAgent",
    "OmtoSupportManagerAgent",
    "ProductionDispatcherAgent",
    "ProductionPreparationEngineerAgent",
    "WarehouseManagerAgent",
]
