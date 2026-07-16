from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.procurement_agent.config import SUPPORTED_AUTONOMY_LEVEL
from app.agents.procurement_agent.policy import evaluate_procurement_action
from app.agents.procurement_agent.state import ProcurementCaseState
from app.models.enums import ProcurementCaseStatus, ProcurementSourceType


COMMON_REQUIRED_FIELDS = ("nomenclature_ref", "quantity", "requested_date")
SOURCE_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    ProcurementSourceType.INTERNAL_CONSUMPTION_ORDER.value: (
        "warehouse_ref",
        "cost_center_ref",
        "expense_item_ref",
    ),
    ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value: (
        "warehouse_ref",
        "production_order_ref",
    ),
    ProcurementSourceType.TRANSFER_ORDER.value: (
        "source_warehouse_ref",
        "target_warehouse_ref",
    ),
    ProcurementSourceType.REORDER_POINT.value: (
        "warehouse_ref",
        "minimum_stock",
        "available_stock",
    ),
}


def validate_request(state: ProcurementCaseState) -> dict[str, Any]:
    warnings = list(state.get("warnings") or [])
    autonomy_level = int(state.get("autonomy_level", 0))
    policy = evaluate_procurement_action(
        state.get("requested_operation") or "assess_need",
        autonomy_level,
    )
    result: dict[str, Any] = {
        "case_status": ProcurementCaseStatus.DATA_CHECK.value,
        "control_point": "KT1",
        "action_class": policy.action_class.value,
        "warnings": warnings,
    }
    if autonomy_level != SUPPORTED_AUTONOMY_LEVEL:
        result.update(
            case_status=ProcurementCaseStatus.FAILED.value,
            recommendation="Используйте уровень автономности 0 для текущего этапа.",
            warnings=[
                *warnings,
                f"Версия графа поддерживает только уровень {SUPPORTED_AUTONOMY_LEVEL}.",
            ],
        )
    elif not policy.allowed:
        result.update(
            case_status=ProcurementCaseStatus.FAILED.value,
            recommendation=policy.reason,
            required_approval=(
                {
                    "action": state.get("requested_operation") or "assess_need",
                    "action_class": policy.action_class.value,
                    "approver_roles": [state.get("human_role") or "authorized_user"],
                    "reason": policy.reason,
                }
                if policy.requires_human
                else None
            ),
        )
    return result


def check_data_quality(state: ProcurementCaseState) -> dict[str, Any]:
    source_data = state.get("source_data") or {}
    required = (
        *COMMON_REQUIRED_FIELDS,
        *SOURCE_REQUIRED_FIELDS.get(state.get("source_type") or "", ()),
    )
    missing_fields = [field for field in required if source_data.get(field) in (None, "", [])]
    if missing_fields:
        return {
            "missing_fields": missing_fields,
            "recommendation": "Заполните обязательные реквизиты исходного документа 1С.",
            "facts": [
                {
                    "kind": "data_quality",
                    "complete": False,
                    "missing_fields": missing_fields,
                }
            ],
        }
    return {
        "missing_fields": [],
        "facts": [
            {
                "kind": "data_quality",
                "complete": True,
                "checked_fields": list(required),
            }
        ],
    }


def prepare_coverage_observation(state: ProcurementCaseState) -> dict[str, Any]:
    source_data = state.get("source_data") or {}
    return {
        "case_status": ProcurementCaseStatus.COVERAGE_CHECK.value,
        "control_point": "KT1",
        "recommendation": (
            "Исходные данные достаточны. Следующий инкремент должен получить остатки, "
            "резервы и открытые заказы через read-only MCP 1С."
        ),
        "rule_refs": ["PROC-L0-READONLY", "PROC-KT1-DATA-QUALITY"],
        "facts": [
            *(state.get("facts") or []),
            {
                "kind": "request",
                "nomenclature_ref": source_data.get("nomenclature_ref"),
                "quantity": source_data.get("quantity"),
                "requested_date": source_data.get("requested_date"),
            },
        ],
        "next_agent": "procurement_need_supervisor",
        "next_control_point": "KT1",
    }


def finalize_result(state: ProcurementCaseState) -> dict[str, Any]:
    status = state.get("case_status") or ProcurementCaseStatus.FAILED.value
    missing = state.get("missing_fields") or []
    if status == ProcurementCaseStatus.FAILED.value:
        summary = "Запрос остановлен политикой безопасности или версией графа."
    elif missing:
        summary = "Проверка качества данных выявила незаполненные обязательные реквизиты."
    else:
        summary = "Кейс прошёл проверку данных и готов к read-only проверке покрытия."
    return {"summary": summary}


def _after_validation(state: ProcurementCaseState) -> str:
    return "finalize_result" if state.get("case_status") == ProcurementCaseStatus.FAILED.value else "check_data_quality"


def _after_data_quality(state: ProcurementCaseState) -> str:
    return "finalize_result" if state.get("missing_fields") else "prepare_coverage_observation"


NODE_SEQUENCE = [
    ("validate_request", validate_request),
    ("check_data_quality", check_data_quality),
    ("prepare_coverage_observation", prepare_coverage_observation),
    ("finalize_result", finalize_result),
]


def build_graph():
    graph = StateGraph(ProcurementCaseState)
    for name, node in NODE_SEQUENCE:
        graph.add_node(name, node)
    graph.set_entry_point("validate_request")
    graph.add_conditional_edges("validate_request", _after_validation)
    graph.add_conditional_edges("check_data_quality", _after_data_quality)
    graph.add_edge("prepare_coverage_observation", "finalize_result")
    graph.add_edge("finalize_result", END)
    return graph.compile()


procurement_graph = build_graph()


__all__ = ["NODE_SEQUENCE", "build_graph", "procurement_graph"]
