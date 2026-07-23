from __future__ import annotations

from app.models.enums import ProcurementSourceType

PRODUCTION_DISPATCHER_AGENT_ID = "production_dispatcher_agent"
PRODUCTION_PREPARATION_ENGINEER_AGENT_ID = "production_preparation_engineer_agent"
WAREHOUSE_PICKER_AGENT_ID = "warehouse_picker_agent"
PURCHASE_MANAGER_AGENT_ID = "purchase_manager_agent"
OMTO_CHIEF_AGENT_ID = "omto_chief_agent"
DEPARTMENT_INITIATOR_AGENT_ID = "department_initiator_agent"
WAREHOUSE_MANAGER_AGENT_ID = "warehouse_manager_agent"

AGENT_LABELS = {
    PRODUCTION_DISPATCHER_AGENT_ID: "Агент диспетчера производства",
    PRODUCTION_PREPARATION_ENGINEER_AGENT_ID: "Агент закупок и логистики",
    WAREHOUSE_PICKER_AGENT_ID: "ИИ-агент по закупке",
    PURCHASE_MANAGER_AGENT_ID: "ИИ-агент менеджера по закупкам",
    OMTO_CHIEF_AGENT_ID: "Агент начальника ОМТО",
    DEPARTMENT_INITIATOR_AGENT_ID: "Агент руководителя подразделения / инициатора",
    WAREHOUSE_MANAGER_AGENT_ID: "Агент начальника склада",
}

SOURCE_AGENT_MAP = {
    ProcurementSourceType.INTERNAL_CONSUMPTION_ORDER.value: DEPARTMENT_INITIATOR_AGENT_ID,
    ProcurementSourceType.TRANSFER_ORDER.value: WAREHOUSE_MANAGER_AGENT_ID,
    ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value: (
        PRODUCTION_PREPARATION_ENGINEER_AGENT_ID
    ),
    ProcurementSourceType.REORDER_POINT.value: PRODUCTION_DISPATCHER_AGENT_ID,
}

AGENT_VERSION = "0.1.0"


def agent_id_for_source(source_type: str) -> str:
    try:
        return SOURCE_AGENT_MAP[source_type]
    except KeyError as exc:
        raise ValueError(f"Не настроен ролевой агент для основания {source_type!r}") from exc


def agent_label(agent_id: str | None) -> str | None:
    if not agent_id:
        return None
    return AGENT_LABELS.get(agent_id, agent_id)


__all__ = [
    "AGENT_LABELS",
    "AGENT_VERSION",
    "DEPARTMENT_INITIATOR_AGENT_ID",
    "OMTO_CHIEF_AGENT_ID",
    "PRODUCTION_DISPATCHER_AGENT_ID",
    "PRODUCTION_PREPARATION_ENGINEER_AGENT_ID",
    "PURCHASE_MANAGER_AGENT_ID",
    "SOURCE_AGENT_MAP",
    "WAREHOUSE_MANAGER_AGENT_ID",
    "WAREHOUSE_PICKER_AGENT_ID",
    "agent_id_for_source",
    "agent_label",
]
