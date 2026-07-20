from __future__ import annotations

from dataclasses import dataclass

from app.agents.procurement_agent.config import READ_ONLY_TOOL_NAMES
from app.models.enums import ProcurementActionClass


_READ_OPERATIONS = {
    "assess_need",
    "check_data_quality",
    "check_coverage",
    "read_case",
    "read_onec",
    *READ_ONLY_TOOL_NAMES,
}
_DRAFT_OPERATIONS = {
    "prepare_purchase_draft",
    "prepare_rfq",
    "prepare_supplier_order",
    "prepare_receipt",
}
_NOTIFY_OPERATIONS = {
    "notify_deadline",
    "route_decision_card",
    "send_internal_summary",
}
_HUMAN_OPERATIONS = {
    "select_supplier",
    "approve_price",
    "approve_payment",
    "place_supplier_order",
    "approve_quality_result",
    "post_receipt",
}
_FORBIDDEN_OPERATIONS = {
    "sign_document",
    "execute_payment",
    "cancel_management_decision",
    "delete_posted_document",
}


@dataclass(frozen=True)
class ProcurementPolicyDecision:
    action_class: ProcurementActionClass
    allowed: bool
    requires_human: bool
    reason: str


def classify_procurement_action(operation: str) -> ProcurementActionClass:
    normalized = operation.strip().lower()
    if normalized in _FORBIDDEN_OPERATIONS:
        return ProcurementActionClass.FORBIDDEN
    if normalized in _HUMAN_OPERATIONS:
        return ProcurementActionClass.HUMAN
    if normalized in _NOTIFY_OPERATIONS:
        return ProcurementActionClass.NOTIFY
    if normalized in _DRAFT_OPERATIONS:
        return ProcurementActionClass.DRAFT
    if normalized in _READ_OPERATIONS:
        return ProcurementActionClass.READ
    return ProcurementActionClass.HUMAN


def evaluate_procurement_action(operation: str, autonomy_level: int) -> ProcurementPolicyDecision:
    action_class = classify_procurement_action(operation)
    if action_class is ProcurementActionClass.FORBIDDEN:
        return ProcurementPolicyDecision(
            action_class=action_class,
            allowed=False,
            requires_human=False,
            reason="Действие безусловно запрещено для ИИ-агента.",
        )
    if action_class is ProcurementActionClass.HUMAN:
        return ProcurementPolicyDecision(
            action_class=action_class,
            allowed=False,
            requires_human=True,
            reason="Действие выполняется только уполномоченным человеком.",
        )
    if autonomy_level == 0 and action_class is not ProcurementActionClass.READ:
        return ProcurementPolicyDecision(
            action_class=action_class,
            allowed=False,
            requires_human=False,
            reason="Первый этап работает в режиме наблюдения: разрешены только чтение и расчёты.",
        )
    if action_class is ProcurementActionClass.DRAFT and autonomy_level < 1:
        return ProcurementPolicyDecision(action_class, False, False, "Проекты доступны с уровня 1.")
    if action_class is ProcurementActionClass.NOTIFY and autonomy_level < 2:
        return ProcurementPolicyDecision(
            action_class,
            False,
            False,
            "Уведомления и маршрутизация доступны только с уровня 2.",
        )
    return ProcurementPolicyDecision(action_class, True, False, "Действие разрешено политикой.")


def evaluate_procurement_tool(tool_name: str, autonomy_level: int) -> ProcurementPolicyDecision:
    if tool_name not in READ_ONLY_TOOL_NAMES:
        return ProcurementPolicyDecision(
            ProcurementActionClass.FORBIDDEN,
            False,
            False,
            "Инструмент отсутствует в закрытом read-only allowlist закупочного агента.",
        )
    return evaluate_procurement_action(tool_name, autonomy_level)


__all__ = [
    "ProcurementPolicyDecision",
    "classify_procurement_action",
    "evaluate_procurement_action",
    "evaluate_procurement_tool",
]
