from __future__ import annotations

from app.models.enums import ProcurementSourceType

PRODUCTION_DISPATCHER_AGENT_ID = "production_dispatcher_agent"
PRODUCTION_PREPARATION_ENGINEER_AGENT_ID = "production_preparation_engineer_agent"
WAREHOUSE_PICKER_AGENT_ID = "warehouse_picker_agent"
WAREHOUSE_COMPLEX_CHIEF_AGENT_ID = "warehouse_complex_chief_agent"
PURCHASE_MANAGER_AGENT_ID = "purchase_manager_agent"
OMTO_CHIEF_AGENT_ID = "omto_chief_agent"
DEPARTMENT_INITIATOR_AGENT_ID = "department_initiator_agent"
WAREHOUSE_MANAGER_AGENT_ID = "warehouse_manager_agent"
OMTO_SUPPORT_MANAGER_AGENT_ID = "omto_support_manager_agent"
OTK_HEAD_AGENT_ID = "otk_head_agent"
QUALITY_ENGINEER_AGENT_ID = "quality_engineer_agent"
QUALITY_DEPUTY_DIRECTOR_AGENT_ID = "quality_deputy_director_agent"
QUALITY_KPI_AGENT_ID = "quality_kpi_agent"
PROCUREMENT_LOGISTICS_AGENT_ID = "procurement_logistics_agent"
FINANCE_DIRECTOR_AGENT_ID = "finance_director_agent"
EXECUTIVE_DIRECTOR_AGENT_ID = "executive_director_agent"
CHIEF_ACCOUNTANT_AGENT_ID = "chief_accountant_agent"
ACCOUNTANT_AGENT_ID = "accountant_agent"
LEGAL_SPECIALIST_AGENT_ID = "legal_specialist_agent"
CFO_HEAD_AGENT_ID = "cfo_head_agent"

AGENT_LABELS = {
    PRODUCTION_DISPATCHER_AGENT_ID: "Агент диспетчера производства",
    PRODUCTION_PREPARATION_ENGINEER_AGENT_ID: "Агент закупок и логистики",
    WAREHOUSE_PICKER_AGENT_ID: "ИИ-агент по закупке",
    WAREHOUSE_COMPLEX_CHIEF_AGENT_ID: "ИИ-агент по закупкам",
    PURCHASE_MANAGER_AGENT_ID: "ИИ-агент менеджера по закупкам",
    OMTO_CHIEF_AGENT_ID: "Агент начальника ОМТО",
    DEPARTMENT_INITIATOR_AGENT_ID: "Агент руководителя подразделения / инициатора",
    WAREHOUSE_MANAGER_AGENT_ID: "Агент начальника склада",
    OMTO_SUPPORT_MANAGER_AGENT_ID: "Агент менеджера по сопровождению ОМТО",
    OTK_HEAD_AGENT_ID: "Агент начальника ОТК",
    QUALITY_ENGINEER_AGENT_ID: "Агент инженера по качеству",
    QUALITY_DEPUTY_DIRECTOR_AGENT_ID: "Агент заместителя директора по качеству",
    QUALITY_KPI_AGENT_ID: "Агент качества (KPI)",
    PROCUREMENT_LOGISTICS_AGENT_ID: "Агент менеджера по закупкам / ОМТО",
    FINANCE_DIRECTOR_AGENT_ID: "Агент финансового директора",
    EXECUTIVE_DIRECTOR_AGENT_ID: "Агент исполнительного директора",
    CHIEF_ACCOUNTANT_AGENT_ID: "Агент главного бухгалтера",
    ACCOUNTANT_AGENT_ID: "Агент сотрудника бухгалтерии",
    LEGAL_SPECIALIST_AGENT_ID: "Агент специалиста юридической службы",
    CFO_HEAD_AGENT_ID: "Агент руководителя ЦФО",
}

# Агенты, оцениваемые KPI-агентом по §12 (кроме самого KPI-мета-агента).
KPI_EVALUATED_AGENT_IDS = (
    OTK_HEAD_AGENT_ID,
    QUALITY_ENGINEER_AGENT_ID,
    QUALITY_DEPUTY_DIRECTOR_AGENT_ID,
    OMTO_SUPPORT_MANAGER_AGENT_ID,
    PRODUCTION_PREPARATION_ENGINEER_AGENT_ID,
    PRODUCTION_DISPATCHER_AGENT_ID,
    DEPARTMENT_INITIATOR_AGENT_ID,
    WAREHOUSE_MANAGER_AGENT_ID,
    PROCUREMENT_LOGISTICS_AGENT_ID,
    FINANCE_DIRECTOR_AGENT_ID,
    EXECUTIVE_DIRECTOR_AGENT_ID,
    CHIEF_ACCOUNTANT_AGENT_ID,
    ACCOUNTANT_AGENT_ID,
    LEGAL_SPECIALIST_AGENT_ID,
    CFO_HEAD_AGENT_ID,
)

# Case status → quality role agent for incoming-control contour (§6.5 / КТ6).
QUALITY_STATUS_AGENT_MAP = {
    "quality_queued": OTK_HEAD_AGENT_ID,
    "quality_assigned": QUALITY_ENGINEER_AGENT_ID,
    "quality_doc_check": QUALITY_ENGINEER_AGENT_ID,
    "quality_inspection": QUALITY_ENGINEER_AGENT_ID,
    "quality_decision": QUALITY_ENGINEER_AGENT_ID,
    "nonconformity": OTK_HEAD_AGENT_ID,
    "isolated": QUALITY_DEPUTY_DIRECTOR_AGENT_ID,
    "rework": QUALITY_ENGINEER_AGENT_ID,
    "reinspection": QUALITY_ENGINEER_AGENT_ID,
}

QUALITY_ROLE_AGENT_IDS = frozenset(
    {
        OTK_HEAD_AGENT_ID,
        QUALITY_ENGINEER_AGENT_ID,
        QUALITY_DEPUTY_DIRECTOR_AGENT_ID,
    }
)

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


def agent_id_for_quality_status(status: str) -> str | None:
    return QUALITY_STATUS_AGENT_MAP.get(status)


def agent_label(agent_id: str | None) -> str | None:
    if not agent_id:
        return None
    return AGENT_LABELS.get(agent_id, agent_id)


__all__ = [
    "ACCOUNTANT_AGENT_ID",
    "AGENT_LABELS",
    "AGENT_VERSION",
    "CFO_HEAD_AGENT_ID",
    "CHIEF_ACCOUNTANT_AGENT_ID",
    "DEPARTMENT_INITIATOR_AGENT_ID",
    "EXECUTIVE_DIRECTOR_AGENT_ID",
    "FINANCE_DIRECTOR_AGENT_ID",
    "KPI_EVALUATED_AGENT_IDS",
    "LEGAL_SPECIALIST_AGENT_ID",
    "OMTO_CHIEF_AGENT_ID",
    "OMTO_SUPPORT_MANAGER_AGENT_ID",
    "OTK_HEAD_AGENT_ID",
    "PROCUREMENT_LOGISTICS_AGENT_ID",
    "PRODUCTION_DISPATCHER_AGENT_ID",
    "PRODUCTION_PREPARATION_ENGINEER_AGENT_ID",
    "PURCHASE_MANAGER_AGENT_ID",
    "QUALITY_DEPUTY_DIRECTOR_AGENT_ID",
    "QUALITY_ENGINEER_AGENT_ID",
    "QUALITY_KPI_AGENT_ID",
    "QUALITY_ROLE_AGENT_IDS",
    "QUALITY_STATUS_AGENT_MAP",
    "SOURCE_AGENT_MAP",
    "WAREHOUSE_COMPLEX_CHIEF_AGENT_ID",
    "WAREHOUSE_MANAGER_AGENT_ID",
    "WAREHOUSE_PICKER_AGENT_ID",
    "agent_id_for_quality_status",
    "agent_id_for_source",
    "agent_label",
]
