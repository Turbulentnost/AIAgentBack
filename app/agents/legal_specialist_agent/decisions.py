"""Deterministic legal specialist claim decisions from contour4 (no live 1C)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from app.agents.legal_specialist_agent.schemas import LegalSpecialistAgentRequest

DEFAULT_STATE_FEE_THRESHOLD = Decimal("5000")
OutcomeKind = Literal[
    "data_check",
    "not_required",
    "awaiting_claim",
    "contract_review",
]


def add_workdays(start: date, days: int) -> date:
    """Add N business days (Mon–Fri), skipping weekends."""
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def parse_advance_date(advance: dict[str, Any]) -> date | None:
    raw = advance.get("advance_date") or advance.get("date") or advance.get("paid_at")
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    text = str(raw)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def resolve_supplier_id(request: LegalSpecialistAgentRequest) -> str | None:
    return request.case_context.upstream.supplier_id or (request.payload or {}).get(
        "supplier_id"
    )


def extract_claim_draft(request: LegalSpecialistAgentRequest) -> Any:
    return (request.human_payload or {}).get("claim_draft") or (
        request.payload or {}
    ).get("claim_draft")


def build_claim_draft(
    supplier_id: str | None,
    advances: list[dict[str, Any]],
    today: date,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline_response = add_workdays(today, 2)
    deadline_negotiate = add_workdays(today, 3)
    deadline_return_text = add_workdays(today, 10)
    claim_sla = {
        "prepare_days": 2,
        "send_days": 3,
        "return_deadline_workdays": 10,
        "deadline_response": deadline_response.isoformat(),
        "deadline_negotiate": deadline_negotiate.isoformat(),
        "deadline_return_in_claim_text": deadline_return_text.isoformat(),
        "calendar_vs_workdays": "workdays",
    }
    claim_draft = {
        "supplier_id": supplier_id,
        "claim_date": today.isoformat(),
        "sla_response_days": 2,
        "sla_negotiate_days": 3,
        "sla_return_workdays": 10,
        "deadline_response": deadline_response.isoformat(),
        "deadline_negotiate": deadline_negotiate.isoformat(),
        "deadline_lawsuit": deadline_return_text.isoformat(),
        "open_advances": advances,
        "subject": "Претензия о возврате/зачёте аванса (CLAIM_REQUIRED)",
        "return_deadline_text_workdays": 10,
    }
    return claim_draft, claim_sla


@dataclass
class LegalAssessment:
    kind: OutcomeKind
    supplier_id: str | None
    advances: list[dict[str, Any]]
    claim_draft: dict[str, Any] | None
    claim_sla: dict[str, Any] | None
    suggested_action: str | None
    logs: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    contract_critical_remarks: list[str] = field(default_factory=list)


def resolve_contract_critical_remarks(
    request: LegalSpecialistAgentRequest,
) -> list[str]:
    remarks = list(request.case_context.contract_critical_remarks or [])
    raw = (request.payload or {}).get("contract_critical_remarks")
    if isinstance(raw, list):
        remarks.extend(str(item) for item in raw if item)
    elif isinstance(raw, str) and raw.strip():
        remarks.append(raw.strip())
    # de-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for item in remarks:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def assess_case(request: LegalSpecialistAgentRequest) -> LegalAssessment:
    logs: list[str] = []
    supplier_id = resolve_supplier_id(request)
    if not supplier_id:
        logs.append("Нет upstream.supplier_id / payload.supplier_id")
        return LegalAssessment(
            kind="data_check",
            supplier_id=None,
            advances=[],
            claim_draft=None,
            claim_sla=None,
            suggested_action="request_clarification",
            logs=logs,
            missing_fields=["upstream.supplier_id"],
        )

    advances = list(request.case_context.open_advances or [])
    remarks = resolve_contract_critical_remarks(request)
    logs.append(f"Открытых авансов: {len(advances)}")
    if request.case_context.upstream.contract_status:
        logs.append(f"contract={request.case_context.upstream.contract_status}")
    if remarks:
        logs.append(f"Крит. замечания по договору (ПЛ-34-048): {len(remarks)}")

    if not advances and remarks:
        return LegalAssessment(
            kind="contract_review",
            supplier_id=supplier_id,
            advances=[],
            claim_draft=None,
            claim_sla=None,
            suggested_action="review_contract_remarks",
            logs=logs,
            contract_critical_remarks=remarks,
        )

    if not advances:
        return LegalAssessment(
            kind="not_required",
            supplier_id=supplier_id,
            advances=[],
            claim_draft=None,
            claim_sla=None,
            suggested_action=None,
            logs=logs,
        )

    today = date.today()
    draft, sla = build_claim_draft(supplier_id, advances, today)
    payload_draft = (request.payload or {}).get("claim_draft")
    if isinstance(payload_draft, dict):
        draft = {**draft, **payload_draft}
    return LegalAssessment(
        kind="awaiting_claim",
        supplier_id=supplier_id,
        advances=advances,
        claim_draft=draft,
        claim_sla=sla,
        suggested_action="approve_claim_draft",
        logs=logs,
        contract_critical_remarks=remarks,
    )


def build_output_from_assessment(assessment: LegalAssessment) -> dict[str, Any]:
    if assessment.kind == "not_required":
        return {
            "claim_draft": None,
            "lawsuit_pack": None,
            "claim_status": "not_required",
            "open_advances_count": 0,
            "supplier_id": assessment.supplier_id,
            "contract_critical_remarks": assessment.contract_critical_remarks,
            "norm_refs": ["СТО-28-020 претензии"],
            "logs": assessment.logs,
        }
    if assessment.kind == "contract_review":
        return {
            "claim_draft": None,
            "lawsuit_pack": None,
            "claim_status": "contract_review",
            "open_advances_count": 0,
            "supplier_id": assessment.supplier_id,
            "contract_critical_remarks": assessment.contract_critical_remarks,
            "risks": ["CONTRACT_CRITICAL_REMARKS"],
            "norm_refs": ["ПЛ-34-048", "Zone 2 критические замечания по договору"],
            "logs": assessment.logs,
        }
    risks = ["CLAIM_REQUIRED"]
    if assessment.contract_critical_remarks:
        risks.append("CONTRACT_CRITICAL_REMARKS")
    return {
        "claim_draft": assessment.claim_draft,
        "claim_sla": assessment.claim_sla,
        "lawsuit_pack": None,
        "claim_status": "draft",
        "open_advances_count": len(assessment.advances),
        "supplier_id": assessment.supplier_id,
        "contract_critical_remarks": assessment.contract_critical_remarks,
        "risks": risks,
        "requires_escalation": True,
        "escalation_reason_code": "CLAIM_REQUIRED",
        "norm_refs": ["СТО-28-020 претензии", "CLAIM_REQUIRED"],
        "logs": assessment.logs,
    }


def _lawsuit_blocked_early(
    advances: list[dict[str, Any]],
    draft: Any,
    logs: list[str],
) -> dict[str, Any] | None:
    first = advances[0] if advances else {}
    advance_date = parse_advance_date(first) if isinstance(first, dict) else None
    if not advance_date or (date.today() - advance_date).days >= 30:
        return None
    logs.append(f"lawsuit blocked: не прошло 1 мес. с {advance_date.isoformat()}")
    return {
        "claim_draft": draft,
        "claim_status": "lawsuit_blocked_early",
        "lawsuit_pack": None,
        "requires_escalation": True,
        "escalation_reason_code": "CLAIM_REQUIRED",
        "logs": logs + ["human_action=prepare_lawsuit blocked: <30d"],
        "norm_refs": ["СТО-28-020 §6.11.2"],
    }


def _lawsuit_blocked_fee(
    request: LegalSpecialistAgentRequest,
    advances: list[dict[str, Any]],
    draft: Any,
    logs: list[str],
) -> dict[str, Any] | None:
    first = advances[0] if advances else {}
    fee_threshold = Decimal(
        str(
            (request.payload or {}).get(
                "state_fee_threshold", DEFAULT_STATE_FEE_THRESHOLD
            )
        )
    )
    amount = Decimal(str((first or {}).get("amount", 0) or 0))
    if not advances or amount > fee_threshold:
        return None
    logs.append(f"lawsuit blocked: amount={amount} <= госпошлина {fee_threshold}")
    return {
        "claim_draft": draft,
        "claim_status": "lawsuit_blocked_fee",
        "lawsuit_pack": None,
        "logs": logs + ["human_action=prepare_lawsuit blocked: fee"],
        "norm_refs": ["СТО-28-020 §6.11.2"],
    }


def apply_human_action(
    request: LegalSpecialistAgentRequest,
) -> tuple[str, str, dict[str, Any], list[str]]:
    """Returns (role_status, summary, output_data, next_roles)."""
    action = (request.human_action or "").lower().strip()
    logs = [f"human_action={action}"]
    draft = extract_claim_draft(request)
    advances = list(request.case_context.open_advances or [])

    if action in {"approve_claim_draft", "approve", "отправить"}:
        return (
            "completed",
            "Претензия утверждена юристом (отправка — через оркестратор)",
            {
                "claim_draft": draft,
                "claim_status": "approved",
                "lawsuit_pack": None,
                "logs": logs + ["human_action=approve_claim_draft"],
                "norm_refs": ["СТО-28-020 претензии"],
            },
            [],
        )

    if action in {"prepare_lawsuit", "lawsuit"}:
        blocked = _lawsuit_blocked_early(advances, draft, logs)
        if blocked is not None:
            return (
                "blocked",
                "Иск возможен только после 1 месяца невозврата (§6.11.2)",
                blocked,
                [],
            )
        blocked = _lawsuit_blocked_fee(request, advances, draft, logs)
        if blocked is not None:
            return (
                "blocked",
                "Сумма аванса не превышает госпошлину — иск нецелесообразен (§6.11.2)",
                blocked,
                [],
            )
        first = advances[0] if advances else {}
        fee_threshold = Decimal(
            str(
                (request.payload or {}).get(
                    "state_fee_threshold", DEFAULT_STATE_FEE_THRESHOLD
                )
            )
        )
        amount = Decimal(str((first or {}).get("amount", 0) or 0))
        pack = {
            "prepared_at": date.today().isoformat(),
            "based_on_claim": draft,
            "status": "pack_ready",
            "advance_amount": str(amount),
            "state_fee_threshold": str(fee_threshold),
        }
        return (
            "completed",
            "Пакет для иска подготовлен",
            {
                "claim_draft": draft,
                "lawsuit_pack": pack,
                "claim_status": "lawsuit_pack",
                "logs": logs + ["human_action=prepare_lawsuit"],
                "norm_refs": ["СТО-28-020 претензии"],
            },
            [],
        )

    if action in {
        "review_contract_remarks",
        "acknowledge_contract_remarks",
        "contract_remarks_ok",
    }:
        remarks = resolve_contract_critical_remarks(request)
        return (
            "completed",
            "Юрист зафиксировал резолюцию по критическим замечаниям договора",
            {
                "claim_status": "contract_remarks_reviewed",
                "contract_critical_remarks": remarks,
                "logs": logs + ["human_action=review_contract_remarks"],
                "norm_refs": ["ПЛ-34-048"],
            },
            [],
        )

    if action in {"return", "reject"}:
        return (
            "blocked",
            "Претензия возвращена на доработку",
            {
                "claim_status": "returned",
                "reject_reason": request.human_payload.get("comment"),
                "logs": logs,
                "norm_refs": ["СТО-28-020 претензии"],
            },
            [],
        )

    return (
        "waiting_human",
        "Ожидается решение юриста",
        {
            "claim_status": "draft",
            "unknown_human_action": action,
            "requires_escalation": True,
            "escalation_reason_code": "CLAIM_REQUIRED",
            "logs": logs,
        },
        [],
    )


__all__ = [
    "DEFAULT_STATE_FEE_THRESHOLD",
    "LegalAssessment",
    "add_workdays",
    "apply_human_action",
    "assess_case",
    "build_claim_draft",
    "build_output_from_assessment",
    "extract_claim_draft",
    "resolve_contract_critical_remarks",
]
