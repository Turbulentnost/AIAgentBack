"""Deterministic accountant payment decisions from contour4 (no live 1C)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from app.agents.accountant_agent.schemas import (
    AccountantAgentRequest,
    AccountantCaseContext,
)

NOTIFY_CONTOURS_ON_PAID = ("contour5",)
OutcomeKind = Literal[
    "data_check",
    "blocked",
    "already_paid",
    "cancel_pending",
    "overdue",
    "queue",
]


@dataclass
class AccountantAssessment:
    kind: OutcomeKind
    payment_request_id: str | None
    payment_status: str
    planned: date | None
    actual: date | None
    delay_days: int
    overdue: bool
    delivery: str | None
    suggested_action: str | None
    risks: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)


def is_cancel_requested(request: AccountantAgentRequest) -> bool:
    ctx = request.case_context
    trigger_l = (request.trigger or "").lower()
    return bool(ctx.cancel_requested) or bool(
        (request.payload or {}).get("cancel_requested")
    ) or trigger_l in {"cancel", "cancel_payment", "payment_cancel"}


def compute_delay_and_overdue(
    planned: date | None,
    actual: date | None,
    payment_status: str,
    today: date,
) -> tuple[int, bool, str]:
    status = (payment_status or "planned").lower()
    delay_days = 0
    if planned and status != "paid":
        delay_days = max(0, (today - planned).days)
    elif planned and actual:
        delay_days = max(0, (actual - planned).days)

    overdue = delay_days > 0 and status != "paid"
    if overdue:
        status = "overdue"
    return delay_days, overdue, status


def resolve_delivery(
    ctx: AccountantCaseContext,
    overdue: bool,
    planned: date | None,
    delay_days: int,
) -> str | None:
    if ctx.recalculated_delivery_date is not None:
        return ctx.recalculated_delivery_date.isoformat()
    if overdue and planned:
        base_need = ctx.production_need_date or planned
        return (base_need + timedelta(days=delay_days)).isoformat()
    return None


def assess_case(request: AccountantAgentRequest) -> AccountantAssessment:
    ctx = request.case_context
    logs: list[str] = []

    if not ctx.payment_request_id:
        logs.append("Нет payment_request_id в case_context")
        return AccountantAssessment(
            kind="data_check",
            payment_request_id=None,
            payment_status="unknown",
            planned=None,
            actual=None,
            delay_days=0,
            overdue=False,
            delivery=None,
            suggested_action="request_clarification",
            logs=logs,
            missing_fields=["payment_request_id"],
        )

    if not ctx.fully_approved:
        logs.append("Заявка не fully_approved — оплата запрещена")
        return AccountantAssessment(
            kind="blocked",
            payment_request_id=ctx.payment_request_id,
            payment_status="blocked",
            planned=ctx.payment_planned_date,
            actual=ctx.payment_actual_date,
            delay_days=0,
            overdue=False,
            delivery=None,
            suggested_action=None,
            logs=logs,
        )

    pr_id = ctx.payment_request_id
    today = date.today()
    planned = ctx.payment_planned_date
    actual = ctx.payment_actual_date
    status = (ctx.payment_status or "planned").lower()
    delay_days, overdue, status = compute_delay_and_overdue(
        planned, actual, status, today
    )
    delivery = resolve_delivery(ctx, overdue, planned, delay_days)
    logs.append(
        f"payment_status={status}; delay_days={delay_days}; overdue={overdue}"
    )

    if is_cancel_requested(request):
        logs.append("cancel_requested → HITL §6.11.6")
        return AccountantAssessment(
            kind="cancel_pending",
            payment_request_id=pr_id,
            payment_status="cancel_pending",
            planned=planned,
            actual=actual,
            delay_days=delay_days,
            overdue=overdue,
            delivery=delivery,
            suggested_action="cancel",
            logs=logs,
        )

    if status == "paid":
        return AccountantAssessment(
            kind="already_paid",
            payment_request_id=pr_id,
            payment_status="paid",
            planned=planned,
            actual=actual or today,
            delay_days=delay_days,
            overdue=False,
            delivery=delivery,
            suggested_action=None,
            logs=logs,
        )

    if overdue:
        return AccountantAssessment(
            kind="overdue",
            payment_request_id=pr_id,
            payment_status="overdue",
            planned=planned,
            actual=actual,
            delay_days=delay_days,
            overdue=True,
            delivery=delivery,
            suggested_action="escalate_overdue",
            risks=["PAYMENT_OVERDUE"],
            logs=logs,
        )

    return AccountantAssessment(
        kind="queue",
        payment_request_id=pr_id,
        payment_status=status,
        planned=planned,
        actual=actual,
        delay_days=delay_days,
        overdue=False,
        delivery=delivery,
        suggested_action="mark_paid",
        logs=logs,
    )


def build_output_from_assessment(
    assessment: AccountantAssessment,
    *,
    amount: Any = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "payment_status": assessment.payment_status,
        "payment_request_id": assessment.payment_request_id,
        "payment_delay_days": assessment.delay_days,
        "payment_planned_date": (
            assessment.planned.isoformat() if assessment.planned else None
        ),
        "payment_actual_date": (
            assessment.actual.isoformat() if assessment.actual else None
        ),
        "recalculated_delivery_date": assessment.delivery,
        "risks": assessment.risks,
        "norm_refs": ["СТО-28-020 §6.11"],
        "logs": assessment.logs,
    }
    if amount is not None:
        out["amount"] = str(amount)
    if assessment.kind == "already_paid":
        out["notify_contours"] = list(NOTIFY_CONTOURS_ON_PAID)
        out["norm_refs"] = ["СТО-28-020 §6.11", "контур №5"]
    if assessment.kind == "overdue":
        out["block_payment"] = True
        out["requires_escalation"] = True
        out["escalation_reason_code"] = "PAYMENT_OVERDUE"
        out["norm_refs"] = ["СТО-28-020 §6.11", "агент рисков №7"]
    if assessment.kind in {"queue", "cancel_pending"}:
        out["block_payment"] = True
    if assessment.kind == "cancel_pending":
        out["norm_refs"] = ["СТО-28-020 §6.11.6"]
    if assessment.kind == "blocked":
        out["block_payment"] = True
    return out


def apply_human_action(
    request: AccountantAgentRequest,
) -> tuple[str, str, dict[str, Any], list[str]]:
    """Returns (role_status, summary, output_data, next_roles)."""
    action = (request.human_action or "").lower().strip()
    logs = [f"human_action={action}"]
    today = date.today().isoformat()
    pr_id = request.case_context.payment_request_id

    if action in {"mark_paid", "paid", "оплачено"}:
        return (
            "completed",
            "Оплата подтверждена человеком",
            {
                "payment_status": "paid",
                "payment_request_id": pr_id,
                "payment_actual_date": today,
                "payment_delay_days": 0,
                "notify_contours": list(NOTIFY_CONTOURS_ON_PAID),
                "logs": logs + ["human_action=mark_paid"],
                "norm_refs": ["СТО-28-020 §6.11", "контур №5"],
            },
            [],
        )

    if action in {"defer", "отложить"}:
        return (
            "completed",
            "Оплата отложена бухгалтером",
            {
                "payment_status": "deferred",
                "payment_request_id": pr_id,
                "block_payment": True,
                "logs": logs,
                "norm_refs": ["СТО-28-020 §6.11"],
            },
            [],
        )

    if action in {"cancel", "отменить", "cancel_payment"}:
        return (
            "completed",
            "Отмена платежа подтверждена бухгалтером (§6.11.6)",
            {
                "payment_status": "cancelled",
                "payment_request_id": pr_id,
                "block_payment": True,
                "reject_reason": request.human_payload.get("comment")
                or (request.payload or {}).get("cancel_reason"),
                "logs": logs + ["human_action=cancel §6.11.6"],
                "norm_refs": ["СТО-28-020 §6.11.6"],
            },
            [],
        )

    return (
        "waiting_human",
        "Ожидается действие бухгалтера",
        {
            "payment_status": "pending",
            "unknown_human_action": action,
            "block_payment": True,
            "logs": logs,
        },
        [],
    )


__all__ = [
    "AccountantAssessment",
    "NOTIFY_CONTOURS_ON_PAID",
    "apply_human_action",
    "assess_case",
    "build_output_from_assessment",
    "compute_delay_and_overdue",
    "is_cancel_requested",
]
