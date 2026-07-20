"""Deterministic CFO head decisions from contour4 (no LLM / no live 1C in this step)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.agents.cfo_head_agent.schemas import (
    CfoCaseContext,
    CfoHeadAgentRequest,
    PaymentMode,
)
from app.agents.cfo_head_agent.sto_dates import (
    calc_payment_planned_date,
    check_lead_time_mismatch,
    validate_payment_date_not_before_next_workday,
)

# Contour4 IO matrix: after successful CFO approve
NEXT_ROLES_ON_SUCCESS = ("finance_director_agent",)


@dataclass
class CfoAssessment:
    amount: Decimal
    ds_limit: Decimal
    ds_ok: bool
    staged_issue: bool
    suggested_action: str
    suggested_payment_date: str | None
    risks: list[str]
    logs: list[str]
    missing_fields: list[str]
    lead_time_mismatch: bool = False


def check_staged_issue(ctx: CfoCaseContext, logs: list[str]) -> bool:
    if ctx.payment_mode is not PaymentMode.STAGED:
        return False
    if not ctx.payment_stages:
        logs.append("staged без этапов — нарушение §6.11.5")
        return True
    total_pct = sum(Decimal(str(stage.stage_pct)) for stage in ctx.payment_stages)
    if len(ctx.payment_stages) == 1 and total_pct >= Decimal("100"):
        logs.append("staged: одна заявка на 100% — нарушение §6.11.5")
        return True
    return False


def resolve_delivery_days(request: CfoHeadAgentRequest, ctx: CfoCaseContext) -> int | None:
    if ctx.delivery_days is not None:
        return int(ctx.delivery_days)
    raw = (request.payload or {}).get("delivery_days")
    if raw is not None and raw != "":
        return int(raw)
    return None


def resolve_lead_time_vvz_days(
    request: CfoHeadAgentRequest, ctx: CfoCaseContext
) -> int | None:
    if ctx.lead_time_vvz_days is not None:
        return int(ctx.lead_time_vvz_days)
    raw = (request.payload or {}).get("lead_time_vvz_days")
    if raw is not None and raw != "":
        return int(raw)
    return None


def compute_suggested_payment_date(
    request: CfoHeadAgentRequest,
    ctx: CfoCaseContext,
    logs: list[str],
) -> str | None:
    if not ctx.production_need_date:
        return None
    delivery_days = resolve_delivery_days(request, ctx)
    if delivery_days is None:
        logs.append(
            "§6.11.4: нет delivery_days — suggested_payment_date не рассчитан "
            "(нужен счёт/договор/КП)"
        )
        return None
    suggested = calc_payment_planned_date(
        ctx.production_need_date,
        delivery_days=delivery_days,
    )
    logs.append(
        f"§6.11.4 suggested_payment_date={suggested.isoformat()} "
        f"(delivery_days={delivery_days})"
    )
    return suggested.isoformat()


def collect_payment_date_risks(
    request: CfoHeadAgentRequest,
    ctx: CfoCaseContext,
    logs: list[str],
) -> tuple[list[str], bool]:
    risks: list[str] = []
    lead_time_mismatch = False
    delivery_days = resolve_delivery_days(request, ctx)
    if ctx.production_need_date and delivery_days is None:
        risks.append("MISSING_DELIVERY_DAYS")
        logs.append("§6.11.4: отсутствует срок поставки (delivery_days)")
    if ctx.payment_planned_date and not validate_payment_date_not_before_next_workday(
        ctx.payment_planned_date, date.today()
    ):
        risks.append("PAYMENT_DATE_TOO_EARLY")
        logs.append("§6.11.3: дата оплаты раньше следующего рабочего дня")
    vvz_days = resolve_lead_time_vvz_days(request, ctx)
    if delivery_days is not None and vvz_days is not None:
        lead_time_mismatch = check_lead_time_mismatch(delivery_days, vvz_days)
        if lead_time_mismatch:
            risks.append("LEAD_TIME_MISMATCH")
            logs.append(
                f"СТО-14-040 §6.9: delivery_days={delivery_days} vs ВВЗ={vvz_days} "
                "(>14) — lead_time_mismatch; ВВЗ не изменяется"
            )
        else:
            logs.append(
                f"СТО-14-040 §6.9: delivery_days={delivery_days} vs ВВЗ={vvz_days} — ок"
            )
    elif delivery_days is not None and vvz_days is None:
        logs.append("СТО-14-040 §6.9: lead_time_vvz_days отсутствует — сверка ВВЗ пропущена")
    return risks, lead_time_mismatch


def assess_case(request: CfoHeadAgentRequest) -> CfoAssessment:
    """Pure assessment from embedded case_context (mock-friendly)."""
    ctx = request.case_context
    logs: list[str] = []
    missing: list[str] = []

    if ctx.amount is None:
        missing.append("amount")
    if ctx.ds_limit is None:
        missing.append("ds_limit")
    if missing:
        logs.append(f"Не хватает полей case_context: {', '.join(missing)}")
        return CfoAssessment(
            amount=Decimal("0"),
            ds_limit=Decimal("0"),
            ds_ok=False,
            staged_issue=False,
            suggested_action="return",
            suggested_payment_date=None,
            risks=[],
            logs=logs,
            missing_fields=missing,
            lead_time_mismatch=False,
        )

    amount = Decimal(str(ctx.amount))
    ds_limit = Decimal(str(ctx.ds_limit))
    ds_ok = amount <= ds_limit
    logs.append(f"Лимит ДС ЦФО={ds_limit}; сумма={amount}; ok={ds_ok}")

    staged_issue = check_staged_issue(ctx, logs)
    suggested_payment_date = compute_suggested_payment_date(request, ctx, logs)
    risks, lead_time_mismatch = collect_payment_date_risks(request, ctx, logs)
    if not ds_ok:
        risks.append("LIMIT_EXCEEDED")

    if staged_issue:
        suggested = "return"
    elif ds_ok:
        suggested = "approve"
    else:
        suggested = "reject"

    return CfoAssessment(
        amount=amount,
        ds_limit=ds_limit,
        ds_ok=ds_ok,
        staged_issue=staged_issue,
        suggested_action=suggested,
        suggested_payment_date=suggested_payment_date,
        risks=risks,
        logs=logs,
        missing_fields=[],
        lead_time_mismatch=lead_time_mismatch,
    )


def build_awaiting_output(ctx: CfoCaseContext, assessment: CfoAssessment) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "approval_status": "pending",
        "ds_limit_ok": assessment.ds_ok,
        "cfo_approved": False,
        "amount": str(assessment.amount),
        "ds_limit": str(assessment.ds_limit),
        "staged_issue": assessment.staged_issue,
        "cfo_code": ctx.cfo_code,
        "payment_request_id": ctx.payment_request_id,
        "payment_mode": ctx.payment_mode.value if ctx.payment_mode else None,
        "expense_article": ctx.expense_article,
        "expected_delivery_date": (
            str(ctx.expected_delivery_date) if ctx.expected_delivery_date else None
        ),
        "lead_time_vvz_days": ctx.lead_time_vvz_days,
        "lead_time_mismatch": assessment.lead_time_mismatch,
        "risks": assessment.risks,
        "norm_refs": [
            "СТО-28-020 §6.2",
            "СТО-28-020 §6.11.3–§6.11.5",
            "СТО-14-040 §6.9",
        ],
        "logs": assessment.logs,
    }
    if assessment.suggested_payment_date:
        facts["suggested_payment_date"] = assessment.suggested_payment_date
        facts["payment_date_status"] = "project"
    return facts


def apply_human_action(
    request: CfoHeadAgentRequest,
    assessment: CfoAssessment | None = None,
) -> tuple[str, str, dict[str, Any], list[str]]:
    """
    Returns (role_status, summary, output_data, next_roles).
    role_status: completed | blocked | waiting_human
    """
    action = (request.human_action or "").lower().strip()
    logs = [f"human_action={action}"]

    if action in {"approve", "утвердить"}:
        ds_ok = assessment.ds_ok if assessment else True
        if assessment is None and request.case_context.amount is not None:
            assessed = assess_case(request)
            ds_ok = assessed.ds_ok
            logs.extend(assessed.logs)
        next_roles = list(NEXT_ROLES_ON_SUCCESS)
        return (
            "completed",
            "ЦФО утвердил заявку",
            {
                "approval_status": "approve",
                "cfo_approved": True,
                "ds_limit_ok": ds_ok,
                "requires_escalation": not ds_ok,
                "escalation_reason_code": "LIMIT_EXCEEDED" if not ds_ok else None,
                "logs": logs,
                "norm_refs": ["СТО-28-020 §6.2"],
            },
            next_roles,
        )

    if action in {"return", "вернуть", "reject", "отклонить"}:
        returned = action in {"return", "вернуть"}
        return (
            "blocked",
            "ЦФО отклонил/вернул заявку — оплата заблокирована",
            {
                "approval_status": "return" if returned else "reject",
                "cfo_approved": False,
                "block_payment": True,
                "reject_reason": request.human_payload.get("comment"),
                "logs": logs,
                "norm_refs": ["СТО-28-020 §6.2"],
            },
            [],
        )

    return (
        "waiting_human",
        "Неизвестное human_action — повторный HITL",
        {
            "approval_status": "pending",
            "unknown_human_action": action,
            "logs": logs,
        },
        [],
    )


__all__ = [
    "CfoAssessment",
    "NEXT_ROLES_ON_SUCCESS",
    "apply_human_action",
    "assess_case",
    "build_awaiting_output",
    "check_staged_issue",
    "collect_payment_date_risks",
    "compute_suggested_payment_date",
    "resolve_delivery_days",
    "resolve_lead_time_vvz_days",
]
