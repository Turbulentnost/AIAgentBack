"""Shared metadata contract for warehouse availability role agents.

Picker (МУ №2) and warehouse complex chief (all other production material orders)
reuse the same calculation service but keep separate metadata namespaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.agents.procurement_role_agents.config import (
    OMTO_CHIEF_AGENT_ID,
    WAREHOUSE_COMPLEX_CHIEF_AGENT_ID,
    WAREHOUSE_PICKER_AGENT_ID,
)
from app.agents.warehouse_picker_agent.department import is_montage_section_2_department
from app.models.enums import ProcurementSourceType
from app.models.procurement import ProcurementCase


@dataclass(frozen=True)
class WarehouseAvailabilitySpec:
    agent_id: str
    prefix: str
    output_key: str
    handoff_agent_id: str
    handoff_event: str
    conclusion_event: str
    critical_event: str
    auto_archive_event: str
    actor_label: str
    department_match: Callable[[str | None], bool]

    def key(self, suffix: str) -> str:
        return f"{self.prefix}_{suffix}"


def _is_non_montage_section_2(department_name: str | None) -> bool:
    return not is_montage_section_2_department(department_name)


PICKER_SPEC = WarehouseAvailabilitySpec(
    agent_id=WAREHOUSE_PICKER_AGENT_ID,
    prefix="picker",
    output_key="warehouse_picker_output",
    handoff_agent_id=OMTO_CHIEF_AGENT_ID,
    handoff_event="picker_handoff_to_omto_chief",
    conclusion_event="picker_conclusion_confirmed",
    critical_event="picker_critical_acknowledged",
    auto_archive_event="picker_auto_archived",
    actor_label="кладовщиком-комплектовщиком",
    department_match=is_montage_section_2_department,
)

COMPLEX_CHIEF_SPEC = WarehouseAvailabilitySpec(
    agent_id=WAREHOUSE_COMPLEX_CHIEF_AGENT_ID,
    prefix="complex",
    output_key="warehouse_complex_output",
    handoff_agent_id=OMTO_CHIEF_AGENT_ID,
    handoff_event="complex_handoff_to_omto_chief",
    conclusion_event="complex_conclusion_confirmed",
    critical_event="complex_critical_acknowledged",
    auto_archive_event="complex_auto_archived",
    actor_label="начальником складского комплекса",
    department_match=_is_non_montage_section_2,
)

WAREHOUSE_AVAILABILITY_SPECS = (PICKER_SPEC, COMPLEX_CHIEF_SPEC)
WAREHOUSE_AVAILABILITY_BY_AGENT = {
    spec.agent_id: spec for spec in WAREHOUSE_AVAILABILITY_SPECS
}


def warehouse_availability_spec(agent_id: str | None) -> WarehouseAvailabilitySpec | None:
    if not agent_id:
        return None
    return WAREHOUSE_AVAILABILITY_BY_AGENT.get(agent_id)


def is_warehouse_availability_case(
    case: ProcurementCase,
    spec: WarehouseAvailabilitySpec,
) -> bool:
    metadata = case.case_metadata or {}
    if metadata.get(spec.key("invoked_at")) or metadata.get(spec.output_key):
        return True
    if case.source_type != ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value:
        return False
    if spec.agent_id == WAREHOUSE_PICKER_AGENT_ID:
        return is_montage_section_2_department(case.department_name)
    # Complex chief owns non-MU2 orders that are not already in the picker workspace.
    if metadata.get("picker_invoked_at") or metadata.get("warehouse_picker_output"):
        return False
    return not is_montage_section_2_department(case.department_name)


def clear_workspace_action_keys(metadata: dict[str, Any], spec: WarehouseAvailabilitySpec) -> None:
    for suffix in (
        "workspace_archived_at",
        "archived_bucket",
        "decision_kind",
        "action_at",
        "action_by",
        "confirmed_action",
        "critical_acknowledged_at",
        "critical_acknowledged_by",
    ):
        metadata.pop(spec.key(suffix), None)


def mirror_picker_fields_from_complex(item: dict[str, Any]) -> dict[str, Any]:
    """Expose complex workspace fields under picker_* keys for shared UI panels."""
    mapped = dict(item)
    for src, dst in (
        ("complex_bucket", "picker_bucket"),
        ("complex_bucket_reason", "picker_bucket_reason"),
        ("complex_work_status", "picker_work_status"),
        ("complex_decision_kind", "picker_decision_kind"),
        ("complex_invoked_at", "picker_invoked_at"),
        ("complex_workspace_archived_at", "picker_workspace_archived_at"),
        ("complex_action_at", "picker_action_at"),
        ("complex_critical_acknowledged_at", "picker_critical_acknowledged_at"),
    ):
        if src in mapped:
            mapped[dst] = mapped.get(src)
    return mapped


__all__ = [
    "COMPLEX_CHIEF_SPEC",
    "PICKER_SPEC",
    "WAREHOUSE_AVAILABILITY_BY_AGENT",
    "WAREHOUSE_AVAILABILITY_SPECS",
    "WarehouseAvailabilitySpec",
    "clear_workspace_action_keys",
    "is_warehouse_availability_case",
    "mirror_picker_fields_from_complex",
    "warehouse_availability_spec",
]
