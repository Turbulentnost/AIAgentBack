"""Shared procurement status groups without service-layer import cycles."""

from app.models.enums import ProcurementCaseStatus

ACTIVE_CASE_STATUSES = frozenset(
    {
        ProcurementCaseStatus.NEW.value,
        ProcurementCaseStatus.AGENT_WAITING.value,
        ProcurementCaseStatus.DATA_CHECK.value,
        ProcurementCaseStatus.COVERAGE_CHECK.value,
        ProcurementCaseStatus.HUMAN_REQUIRED.value,
        ProcurementCaseStatus.BLOCKED.value,
        ProcurementCaseStatus.QUALITY_QUEUED.value,
        ProcurementCaseStatus.QUALITY_ASSIGNED.value,
        ProcurementCaseStatus.QUALITY_DOC_CHECK.value,
        ProcurementCaseStatus.QUALITY_INSPECTION.value,
        ProcurementCaseStatus.QUALITY_DECISION.value,
        ProcurementCaseStatus.ISOLATED.value,
        ProcurementCaseStatus.NONCONFORMITY.value,
        ProcurementCaseStatus.REWORK.value,
        ProcurementCaseStatus.REINSPECTION.value,
        ProcurementCaseStatus.QUALITY_RELEASED.value,
    }
)

SOURCE_MONITORED_CASE_STATUSES = ACTIVE_CASE_STATUSES | {
    ProcurementCaseStatus.ORDERED.value,
}
