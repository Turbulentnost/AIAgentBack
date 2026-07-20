"""Deterministic chief accountant decisions from contour4 (no live 1C tools)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents.chief_accountant_agent.schemas import (
    ApprovalsChain,
    ChiefCaseContext,
    ChiefAccountantAgentRequest,
    InvoiceRequisites,
)

NEXT_ROLES_ON_SUCCESS = ("accountant_agent",)


@dataclass
class ChiefAssessment:
    payment_request_id: str | None
    registry_id: str | None
    requisites: InvoiceRequisites
    chain: ApprovalsChain
    issues: list[str]
    suggested_action: str
    logs: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)


def requisites_issues(reqs: InvoiceRequisites, logs: list[str]) -> list[str]:
    issues: list[str] = []
    # Empty snapshot (neither complete nor inn set) still fails completeness
    if reqs.complete is None:
        issues.append("incomplete_requisites")
        logs.append("requisites.complete отсутствует — считаем неполными")
    elif not reqs.complete:
        issues.append("incomplete_requisites")
    if not reqs.inn:
        issues.append("missing_inn")
    return issues


def approval_issues(ctx: ChiefCaseContext, chain: ApprovalsChain) -> list[str]:
    cfo_ok = bool(chain.cfo_approved) or bool(ctx.fully_approved)
    if cfo_ok:
        return []
    return ["missing_cfo_approval"]


def advance_issues(ctx: ChiefCaseContext, logs: list[str]) -> list[str]:
    advances = list(ctx.open_advances or [])
    if ctx.upstream.supplier_id and not advances:
        logs.append(
            f"supplier_id={ctx.upstream.supplier_id}: open_advances из snapshot пусты "
            "(live get_open_advances не вызывается)"
        )
    if not advances:
        return []
    logs.append(f"Открытых авансов: {len(advances)}")
    return ["open_advances"]


def collect_approve_blockers(ctx: ChiefCaseContext) -> list[str]:
    chain = ctx.approvals_chain
    cfo_ok = bool(chain.cfo_approved) or bool(ctx.fully_approved)
    blockers: list[str] = []
    if not cfo_ok:
        blockers.append("missing_cfo_approval")
    if ctx.open_advances:
        blockers.append("open_advances")
    return blockers


def assess_case(request: ChiefAccountantAgentRequest) -> ChiefAssessment:
    ctx = request.case_context
    logs: list[str] = []
    missing_fields: list[str] = []

    if not ctx.registry_id and not ctx.payment_request_id:
        missing_fields.append("registry_id|payment_request_id")
        logs.append("Нужен registry_id или payment_request_id в case_context")
        return ChiefAssessment(
            payment_request_id=None,
            registry_id=None,
            requisites=ctx.invoice_requisites,
            chain=ctx.approvals_chain,
            issues=[],
            suggested_action="request_clarification",
            logs=logs,
            missing_fields=missing_fields,
        )

    if ctx.registry_id:
        logs.append(f"Реестр {ctx.registry_id} (строки реестра не читаются из 1С)")
    pr_id = ctx.payment_request_id
    reqs = ctx.invoice_requisites
    logs.append(f"Реквизиты complete={reqs.complete}")
    chain = ctx.approvals_chain

    issues = requisites_issues(reqs, logs)
    issues.extend(approval_issues(ctx, chain))
    issues.extend(advance_issues(ctx, logs))

    suggested = "approve" if not issues else "return"
    return ChiefAssessment(
        payment_request_id=pr_id,
        registry_id=ctx.registry_id,
        requisites=reqs,
        chain=chain,
        issues=issues,
        suggested_action=suggested,
        logs=logs,
        missing_fields=[],
    )


def build_awaiting_output(
    ctx: ChiefCaseContext,
    assessment: ChiefAssessment,
) -> dict[str, Any]:
    return {
        "accounting_opinion": "pending",
        "issues": assessment.issues,
        "payment_request_id": assessment.payment_request_id,
        "registry_id": assessment.registry_id,
        "invoice_requisites": {
            "complete": assessment.requisites.complete,
            "inn": assessment.requisites.inn,
        },
        "cfo_approved": bool(assessment.chain.cfo_approved) or bool(ctx.fully_approved),
        "open_advances_count": len(ctx.open_advances or []),
        "norm_refs": ["СТО-28-020 §6.2", "СТО-28-020 §6.11"],
        "logs": assessment.logs,
    }


def apply_human_action(
    request: ChiefAccountantAgentRequest,
) -> tuple[str, str, dict[str, Any], list[str]]:
    """Returns (role_status, summary, output_data, next_roles)."""
    action = (request.human_action or "").lower().strip()
    logs = [f"human_action={action}"]
    ctx = request.case_context

    if action in {"approve", "согласовать", "ok"}:
        assessment = assess_case(request)
        if assessment.missing_fields:
            return (
                "data_check",
                "Нельзя согласовать: неполный case_context",
                {
                    "accounting_opinion": "pending",
                    "missing_fields": assessment.missing_fields,
                    "logs": logs,
                    "block_payment": True,
                },
                [],
            )
        blockers = collect_approve_blockers(ctx)
        if blockers:
            return (
                "blocked",
                "Согласование главбуха запрещено — есть блокеры",
                {
                    "accounting_opinion": "blocked",
                    "issues": blockers,
                    "block_payment": True,
                    "logs": logs + [f"human_action=approve blocked: {blockers}"],
                    "norm_refs": ["СТО-28-020 §6.2", "СТО-28-020 §6.11"],
                },
                [],
            )
        return (
            "completed",
            "Главбух согласовал — можно в оплату",
            {
                "accounting_opinion": "ok",
                "issues": [],
                "fully_approved": True,
                "logs": logs + ["human_action=approve"],
                "norm_refs": ["СТО-28-020 §6.2"],
            },
            list(NEXT_ROLES_ON_SUCCESS),
        )

    if action in {"return", "вернуть", "reject"}:
        issues = request.human_payload.get("issues") or ["returned_by_gb"]
        if not isinstance(issues, list):
            issues = [str(issues)]
        return (
            "blocked",
            "Главбух вернул с замечаниями",
            {
                "accounting_opinion": "return_with_issues",
                "issues": issues,
                "fully_approved": False,
                "block_payment": True,
                "reject_reason": request.human_payload.get("comment"),
                "logs": logs,
                "norm_refs": ["СТО-28-020 §6.2"],
            },
            [],
        )

    return (
        "waiting_human",
        "Ожидается решение главбуха",
        {
            "accounting_opinion": "pending",
            "unknown_human_action": action,
            "logs": logs,
        },
        [],
    )


__all__ = [
    "ChiefAssessment",
    "NEXT_ROLES_ON_SUCCESS",
    "apply_human_action",
    "assess_case",
    "build_awaiting_output",
    "collect_approve_blockers",
]
