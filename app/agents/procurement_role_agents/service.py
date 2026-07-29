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
from app.agents.otk_head_agent.service import OtkHeadService
from app.agents.production_dispatcher_agent.service import ProductionDispatcherService
from app.agents.production_preparation_engineer_agent.service import (
    ProductionPreparationEngineerService,
)
from app.agents.purchase_manager_agent.service import PurchaseManagerService
from app.agents.quality_deputy_director_agent.service import QualityDeputyDirectorService
from app.agents.quality_engineer_agent.service import QualityEngineerService
from app.agents.quality_kpi_agent.service import QualityKpiService
from app.agents.warehouse_picker_agent.service import WarehousePickerService
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
    purpose = (
        "Проверяет остатки по точкам заказа и кейсам после инженера, "
        "рассчитывает срочность и рекомендует способ обеспечения."
    )
    allowed_tools = [
        "onec_get_production_supply_evidence",
        "read_production_get_open_supply_documents",
        "onec_get_free_stock",
        "onec_get_open_supplier_orders",
        "onec_get_goods_in_transit",
        "onec_get_internal_transfers",
    ]

    async def run(self, payload: dict) -> ProcurementRoleAgentResult:
        return await ProductionDispatcherService().run(
            payload,
            agent_id=self.agent_id,
        )


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
class WarehousePickerAgent(_WaitingProcurementRoleAgent):
    agent_id = config.WAREHOUSE_PICKER_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = (
        "Проверяет наличие ТМЦ в кладовой монтажного участка №2, "
        "подтверждает выдачу или дефицит и передаёт заключение оркестратору."
    )
    allowed_tools = [
        "onec_get_store_room_stock",
        "onec_get_production_supply_evidence",
        "onec_get_free_stock",
        "onec_get_quality_stock",
    ]

    async def run(self, payload: dict) -> ProcurementRoleAgentResult:
        return await WarehousePickerService().run(
            payload,
            agent_id=self.agent_id,
        )


@agent_registry.register
class WarehouseComplexChiefAgent(_WaitingProcurementRoleAgent):
    agent_id = config.WAREHOUSE_COMPLEX_CHIEF_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = (
        "Начальник складского комплекса: проверяет наличие ТМЦ по заказам материалов "
        "в производство (кроме МУ №2) и формирует заключение для ОМТО."
    )
    allowed_tools = [
        "onec_get_store_room_stock",
        "onec_get_production_supply_evidence",
        "onec_get_free_stock",
        "onec_get_quality_stock",
    ]

    async def run(self, payload: dict) -> ProcurementRoleAgentResult:
        return await WarehousePickerService().run(
            payload,
            agent_id=self.agent_id,
        )


@agent_registry.register
class PurchaseManagerAgent(_WaitingProcurementRoleAgent):
    agent_id = config.PURCHASE_MANAGER_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = (
        "Контролирует связанные заказы поставщику по заказам материалов "
        "и показывает покрытие номенклатур."
    )
    allowed_tools = ["read_procurement_list_supplier_orders"]

    async def run(self, payload: dict) -> ProcurementRoleAgentResult:
        return await PurchaseManagerService().run(payload, agent_id=self.agent_id)


@agent_registry.register
class OmtoChiefAgent(_WaitingProcurementRoleAgent):
    agent_id = config.OMTO_CHIEF_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = "Обработка заключения кладовщика-комплектовщика по закупке ТМЦ."


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


@agent_registry.register
class OtkHeadAgent(_WaitingProcurementRoleAgent):
    agent_id = config.OTK_HEAD_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = (
        "Распределение предъявлений, проверка актов и контроль сроков входного контроля."
    )

    async def run(self, payload: dict) -> ProcurementRoleAgentResult:
        return await OtkHeadService().run(payload, agent_id=self.agent_id)


@agent_registry.register
class QualityEngineerAgent(_WaitingProcurementRoleAgent):
    agent_id = config.QUALITY_ENGINEER_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = (
        "Документарный и физический входной контроль, протоколы и акты несоответствия."
    )

    async def run(self, payload: dict) -> ProcurementRoleAgentResult:
        return await QualityEngineerService().run(payload, agent_id=self.agent_id)


@agent_registry.register
class QualityDeputyDirectorAgent(_WaitingProcurementRoleAgent):
    agent_id = config.QUALITY_DEPUTY_DIRECTOR_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = (
        "Проект резолюции по несоответствующей партии и контроль маршрута исполнения."
    )

    async def run(self, payload: dict) -> ProcurementRoleAgentResult:
        return await QualityDeputyDirectorService().run(payload, agent_id=self.agent_id)


@agent_registry.register
class QualityKpiAgent(_WaitingProcurementRoleAgent):
    agent_id = config.QUALITY_KPI_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = "Оценка работы ИИ-агентов и расчёт KPI по §12 ТЗ."

    async def run(self, payload: dict) -> ProcurementRoleAgentResult:
        return await QualityKpiService().run(payload, agent_id=self.agent_id)


__all__ = [
    "DepartmentInitiatorAgent",
    "OmtoChiefAgent",
    "OmtoSupportManagerAgent",
    "OtkHeadAgent",
    "ProductionDispatcherAgent",
    "ProductionPreparationEngineerAgent",
    "PurchaseManagerAgent",
    "QualityDeputyDirectorAgent",
    "QualityEngineerAgent",
    "QualityKpiAgent",
    "WarehouseManagerAgent",
    "WarehousePickerAgent",
]
