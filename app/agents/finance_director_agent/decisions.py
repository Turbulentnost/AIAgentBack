"""Deterministic finance director decisions from contour4 (no live 1C in this step)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.agents.finance_director_agent.schemas import (
    FinanceCaseContext,
    FinanceDirectorAgentRequest,
)

NEXT_ROLES_ON_ALLOW = ("cfo_head_agent", "executive_director_agent")
SECURITY_ROLE = "security_service"
ONE_OFF_LIMIT = Decimal("10000")


@dataclass
class FinanceAssessment:
    amount: Decimal
    remaining: Decimal
    s10_ok: bool
    esc_code: str
    risks: list[str]
    suggested_action: str
    next_on_allow: list[str]
    one_off: bool
    contract_status: str | None
    logs: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)


def compute_price_delta(ctx: FinanceCaseContext, reason: str) -> bool:
    up = ctx.upstream
    return (
        "PRICE" in reason
        or up.price_match is False
        or (
            up.price_deviation_pct is not None
            and Decimal(str(up.price_deviation_pct)) != 0
        )
    )


def project_price_expired(ctx: FinanceCaseContext) -> bool:
    until = ctx.upstream.project_price_valid_until
    if until is None:
        return False
    return until < date.today()


def monitoring_pack_incomplete(ctx: FinanceCaseContext) -> bool:
    quotes = ctx.upstream.market_quotes_count
    if quotes is None:
        quotes = len(ctx.upstream.supplier_quotes or [])
    return int(quotes) < 2


def compute_esc_code(
    reason: str,
    trigger: str,
    s10_ok: bool,
    one_off: bool,
    price_delta: bool,
) -> str:
    if "URGENT" in reason or "urgent" in trigger:
        return "URGENT_PREPAY"
    if not s10_ok or "S10" in reason:
        return "S10_EXCEEDED"
    if one_off:
        return "ONE_OFF_NO_CONTRACT"
    if price_delta:
        return "PRICE_DELTA"
    if reason:
        return reason
    return "FINANCE_EXCEPTION"


def assess_case(request: FinanceDirectorAgentRequest) -> FinanceAssessment:
    ctx = request.case_context
    logs: list[str] = []
    missing: list[str] = []

    if ctx.amount is None:
        missing.append("amount")
    if missing:
        logs.append(f"Не хватает полей case_context: {', '.join(missing)}")
        return FinanceAssessment(
            amount=Decimal("0"),
            remaining=Decimal("0"),
            s10_ok=False,
            esc_code="DATA_CHECK",
            risks=[],
            suggested_action="request_clarification",
            next_on_allow=[],
            one_off=False,
            contract_status=None,
            logs=logs,
            missing_fields=missing,
        )

    amount = Decimal(str(ctx.amount))
    if ctx.s10_week_remaining is None:
        # Embedded-only mode: treat missing S10 as data_check for remaining
        logs.append("s10_week_remaining отсутствует в case_context")
        return FinanceAssessment(
            amount=amount,
            remaining=Decimal("0"),
            s10_ok=False,
            esc_code="DATA_CHECK",
            risks=[],
            suggested_action="request_clarification",
            next_on_allow=[],
            one_off=False,
            contract_status=ctx.upstream.contract_status,
            logs=logs,
            missing_fields=["s10_week_remaining"],
        )

    remaining = Decimal(str(ctx.s10_week_remaining))
    s10_ok = amount <= remaining
    logs.append(f"S10 week remaining={remaining}; amount={amount}; ok={s10_ok}")

    trigger = (request.trigger or "").lower()
    reason = (ctx.escalation_reason_code or trigger or "").upper()
    one_off = "ONE_OFF" in reason or "one_off" in trigger
    risks: list[str] = []
    if one_off and amount > ONE_OFF_LIMIT:
        logs.append("Разовая без договора > 10000 — исключение неприменимо")
        risks.append("ONE_OFF_OVER_LIMIT")

    price_delta = compute_price_delta(ctx, reason)
    pc_expired = project_price_expired(ctx)
    if pc_expired:
        risks.append("PROJECT_PRICE_EXPIRED")
        logs.append(
            f"Проектная цена просрочена: valid_until={ctx.upstream.project_price_valid_until}"
        )
        price_delta = True
    if (price_delta or pc_expired) and monitoring_pack_incomplete(ctx):
        risks.append("MONITORING_PACK_INCOMPLETE")
        logs.append("Пакет мониторинга < 2 предложений — требуется согласование (§6.4)")
        if ctx.upstream.sz_required is not False:
            risks.append("SZ_REQUIRED")

    esc_code = compute_esc_code(reason, trigger, s10_ok, one_off, price_delta)
    if esc_code and esc_code not in risks:
        risks.append(esc_code)

    suggested = "allow" if s10_ok and "ONE_OFF_OVER_LIMIT" not in risks else "deny"
    next_on_allow: list[str] = []
    if one_off and amount <= ONE_OFF_LIMIT and "ONE_OFF_OVER_LIMIT" not in risks:
        next_on_allow = [SECURITY_ROLE]
        logs.append("Разовая ≤10000 — после allow требуется СБ (шаг 11)")

    if "MONITORING_PACK_INCOMPLETE" in risks or "PROJECT_PRICE_EXPIRED" in risks:
        if suggested == "allow":
            suggested = "defer"
            logs.append("ПЦ/мониторинг неполные — suggested=defer до пакета обоснования")

    return FinanceAssessment(
        amount=amount,
        remaining=remaining,
        s10_ok=s10_ok,
        esc_code=esc_code,
        risks=risks,
        suggested_action=suggested,
        next_on_allow=next_on_allow,
        one_off=one_off,
        contract_status=ctx.upstream.contract_status,
        logs=logs,
        missing_fields=[],
    )


def build_awaiting_output(
    ctx: FinanceCaseContext,
    assessment: FinanceAssessment,
) -> dict[str, Any]:
    up = ctx.upstream
    return {
        "financial_decision": "pending",
        "s10_ok": assessment.s10_ok,
        "amount": str(assessment.amount),
        "s10_week_remaining": str(assessment.remaining),
        "procurement_limit_week_remaining": str(assessment.remaining),
        "escalation_reason_code": assessment.esc_code,
        "contract_status": assessment.contract_status or up.contract_status,
        "invoice_verified": up.invoice_verified,
        "price_match": up.price_match,
        "sz_required": up.sz_required,
        "project_price_valid_until": (
            str(up.project_price_valid_until) if up.project_price_valid_until else None
        ),
        "payment_date_status": ctx.payment_date_status or "project",
        "one_off": assessment.one_off,
        "risks": assessment.risks,
        "next_roles_on_allow": assessment.next_on_allow,
        "norm_refs": [
            "СТО-28-020 §6.9",
            "СТО-14-040 §6.6.1",
            "СТО-28-020 шаг 11",
        ],
        "logs": assessment.logs,
    }


def _cfo_already_resolved(request: FinanceDirectorAgentRequest) -> bool:
    """True when bouncing back to cfo_head would create a cfo↔finance loop."""
    ctx = request.case_context
    payload = request.payload or {}
    if ctx.cfo_approved is True or payload.get("cfo_approved") is True:
        return True
    prior = (ctx.financial_decision or payload.get("financial_decision") or "").lower()
    return prior in {"allow", "deny", "defer"}


def _next_roles_on_allow(request: FinanceDirectorAgentRequest) -> list[str]:
    ctx = request.case_context
    amount = Decimal(str(ctx.amount or 0))
    esc = (ctx.escalation_reason_code or request.trigger or "").upper()
    one_off = "ONE_OFF" in esc or "one_off" in (request.trigger or "").lower()
    roles = list(NEXT_ROLES_ON_ALLOW)
    if _cfo_already_resolved(request):
        roles = [r for r in roles if r != "cfo_head_agent"]
    if one_off and amount <= ONE_OFF_LIMIT:
        roles = [SECURITY_ROLE, *roles]
    return roles


def apply_human_action(
    request: FinanceDirectorAgentRequest,
    assessment: FinanceAssessment | None = None,
) -> tuple[str, str, dict[str, Any], list[str]]:
    """Returns (role_status, summary, output_data, next_roles)."""
    action = (request.human_action or "").lower().strip()
    logs = [f"human_action={action}"]
    s10_ok = assessment.s10_ok if assessment else True
    if assessment is None and request.case_context.amount is not None:
        assessed = assess_case(request)
        if not assessed.missing_fields:
            s10_ok = assessed.s10_ok
            logs.extend(assessed.logs)
    esc = request.case_context.escalation_reason_code or "FINANCE_EXCEPTION"

    if action in {"allow", "approve", "разрешить"}:
        next_roles = _next_roles_on_allow(request)
        return (
            "completed",
            "Финдиректор разрешил исключение",
            {
                "financial_decision": "allow",
                "s10_ok": s10_ok,
                "block_payment": False,
                "logs": logs,
                "norm_refs": ["СТО-28-020 §6.9", "СТО-14-040 §6.6.1"],
            },
            next_roles,
        )

    if action in {"deny", "reject", "отказать"}:
        return (
            "blocked",
            "Финдиректор отказал",
            {
                "financial_decision": "deny",
                "s10_ok": s10_ok,
                "block_payment": True,
                "requires_escalation": True,
                "escalation_reason_code": esc,
                "reject_reason": request.human_payload.get("comment"),
                "logs": logs,
                "norm_refs": ["СТО-28-020 §6.9"],
            },
            [],
        )

    if action in {"defer", "отложить"}:
        return (
            "escalated",
            "Финдиректор отложил решение",
            {
                "financial_decision": "defer",
                "s10_ok": s10_ok,
                "block_payment": True,
                "requires_escalation": True,
                "escalation_reason_code": esc,
                "logs": logs,
                "norm_refs": ["СТО-28-020 §6.9"],
            },
            [],
        )

    return (
        "waiting_human",
        "Ожидается корректная резолюция финдиректора",
        {
            "financial_decision": "pending",
            "unknown_human_action": action,
            "logs": logs,
        },
        [],
    )


__all__ = [
    "FinanceAssessment",
    "NEXT_ROLES_ON_ALLOW",
    "SECURITY_ROLE",
    "apply_human_action",
    "assess_case",
    "build_awaiting_output",
    "compute_esc_code",
    "compute_price_delta",
    "monitoring_pack_incomplete",
    "project_price_expired",
]
