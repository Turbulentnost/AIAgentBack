"""Deterministic executive director decisions from contour4 (no live registry tools)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any

from app.agents.executive_director_agent.schemas import (
    ExecutiveCaseContext,
    ExecutiveDirectorAgentRequest,
    RegistryLine,
)

NEXT_ROLES_ON_SUCCESS = ("chief_accountant_agent",)
REGISTRY_DEADLINE = time(12, 0)


@dataclass
class ExecutiveAssessment:
    registry_id: str
    lines: list[RegistryLine]
    missing_cfo: list[str]
    priorities: list[dict[str, Any]]
    deadline_passed: bool
    risks: list[str]
    suggested_action: str
    logs: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)


def resolve_deadline(request: ExecutiveDirectorAgentRequest, logs: list[str]) -> bool:
    now = datetime.now().time()
    deadline_passed = now > REGISTRY_DEADLINE
    as_of = (request.payload or {}).get("as_of_time")
    if isinstance(as_of, str) and ":" in as_of:
        try:
            parts = as_of.split(":")
            now = time(int(parts[0]), int(parts[1]))
            deadline_passed = now > REGISTRY_DEADLINE
        except (ValueError, IndexError):
            pass
    if deadline_passed:
        logs.append("Просрочен дедлайн согласования реестра 12:00")
    return deadline_passed


def find_missing_cfo(lines: list[RegistryLine]) -> list[str]:
    missing: list[str] = []
    for ln in lines:
        if not ln.cfo_approved:
            missing.append(ln.payment_request_id or "?")
    return missing


def build_line_priorities(lines: list[RegistryLine]) -> list[dict[str, Any]]:
    return [
        {
            "payment_request_id": ln.payment_request_id,
            "priority": 1 if (ln.urgency or "").lower() == "high" else 2,
        }
        for ln in lines
    ]


def assess_case(request: ExecutiveDirectorAgentRequest) -> ExecutiveAssessment:
    ctx = request.case_context
    logs: list[str] = []
    missing_fields: list[str] = []

    if not ctx.registry_id:
        missing_fields.append("registry_id")
    if not ctx.registry_lines:
        missing_fields.append("registry_lines")

    if missing_fields:
        logs.append(f"Не хватает полей case_context: {', '.join(missing_fields)}")
        return ExecutiveAssessment(
            registry_id=ctx.registry_id or "",
            lines=[],
            missing_cfo=[],
            priorities=[],
            deadline_passed=False,
            risks=[],
            suggested_action="request_clarification",
            logs=logs,
            missing_fields=missing_fields,
        )

    lines = list(ctx.registry_lines)
    logs.append(f"Реестр {ctx.registry_id}: строк={len(lines)}; дедлайн 12:00")
    deadline_passed = resolve_deadline(request, logs)
    missing_cfo = find_missing_cfo(lines)
    if missing_cfo:
        logs.append(f"Строки без ЦФО-утверждения: {missing_cfo}")
    if ctx.production_need_date is not None:
        logs.append(f"Дата потребности (контекст): {ctx.production_need_date}")

    priorities = build_line_priorities(lines)
    risks: list[str] = []
    if missing_cfo:
        risks.append("ROUTE_EXCEPTION")
    if deadline_passed:
        risks.append("REGISTRY_DEADLINE_MISSED")

    suggested = "return" if missing_cfo else "approve"
    return ExecutiveAssessment(
        registry_id=ctx.registry_id,
        lines=lines,
        missing_cfo=missing_cfo,
        priorities=priorities,
        deadline_passed=deadline_passed,
        risks=risks,
        suggested_action=suggested,
        logs=logs,
        missing_fields=[],
    )


def build_awaiting_output(
    ctx: ExecutiveCaseContext,
    assessment: ExecutiveAssessment,
) -> dict[str, Any]:
    return {
        "registry_resolution": "pending",
        "registry_id": assessment.registry_id,
        "lines_count": len(assessment.lines),
        "line_priorities": assessment.priorities,
        "missing_cfo": assessment.missing_cfo,
        "registry_deadline_passed": assessment.deadline_passed,
        "risks": assessment.risks,
        "production_need_date": (
            str(ctx.production_need_date) if ctx.production_need_date else None
        ),
        "norm_refs": ["СТО-28-020 §6.2", "СТО-28-020 §6.6"],
        "logs": assessment.logs,
    }


def apply_human_action(
    request: ExecutiveDirectorAgentRequest,
) -> tuple[str, str, dict[str, Any], list[str]]:
    """Returns (role_status, summary, output_data, next_roles)."""
    action = (request.human_action or "").lower().strip()
    logs = [f"human_action={action}"]
    assessment = assess_case(request)
    if not assessment.missing_fields:
        logs.extend(assessment.logs)

    if action in {"approve", "утвердить", "approve_registry"}:
        if assessment.missing_fields:
            return (
                "data_check",
                "Нельзя утвердить: неполный case_context реестра",
                {
                    "registry_resolution": "pending",
                    "missing_fields": assessment.missing_fields,
                    "logs": logs,
                    "block_payment": True,
                },
                [],
            )
        if assessment.missing_cfo:
            return (
                "blocked",
                "Утверждение реестра запрещено: есть строки без ЦФО",
                {
                    "registry_resolution": "blocked_missing_cfo",
                    "missing_cfo": assessment.missing_cfo,
                    "block_payment": True,
                    "requires_escalation": True,
                    "escalation_reason_code": "ROUTE_EXCEPTION",
                    "risks": ["ROUTE_EXCEPTION"],
                    "logs": logs + ["human_action=approve blocked: missing_cfo"],
                    "norm_refs": ["СТО-28-020 §6.2", "СТО-28-020 §6.6"],
                },
                [],
            )
        return (
            "completed",
            "ИД утвердил реестр оплат",
            {
                "registry_resolution": "approved",
                "registry_id": assessment.registry_id,
                "line_priorities": assessment.priorities,
                "logs": logs + ["human_action=approve_registry"],
                "norm_refs": ["СТО-28-020 §6.2"],
            },
            list(NEXT_ROLES_ON_SUCCESS),
        )

    if action in {"return", "вернуть", "reject"}:
        return (
            "blocked",
            "ИД вернул реестр ОМТО",
            {
                "registry_resolution": "returned",
                "block_payment": True,
                "reject_reason": request.human_payload.get("comment"),
                "logs": logs,
                "norm_refs": ["СТО-28-020 §6.2"],
            },
            [],
        )

    if action in {"set_priority", "set_line_priorities", "приоритет"}:
        overrides = (
            request.human_payload.get("line_priorities")
            or request.human_payload.get("priorities")
        )
        if not isinstance(overrides, list) or not overrides:
            return (
                "waiting_human",
                "Для set_priority нужны line_priorities в human_payload",
                {
                    "registry_resolution": "pending",
                    "line_priorities": assessment.priorities,
                    "logs": logs + ["set_priority rejected: empty line_priorities"],
                    "norm_refs": ["СТО-28-020 §6.2"],
                },
                [],
            )
        # Zone 2: приоритет платежа — явная резолюция человека; реестр ещё не утверждён
        return (
            "waiting_human",
            "ИД зафиксировал приоритеты строк реестра — требуется утверждение реестра",
            {
                "registry_resolution": "pending",
                "line_priorities": overrides,
                "priority_set_by_human": True,
                "logs": logs + ["human_action=set_priority"],
                "norm_refs": ["СТО-28-020 §6.2", "Zone 2 приоритет платежа"],
            },
            [],
        )

    return (
        "waiting_human",
        "Ожидается корректная резолюция исполнительного директора",
        {
            "registry_resolution": "pending",
            "unknown_human_action": action,
            "logs": logs,
        },
        [],
    )


__all__ = [
    "ExecutiveAssessment",
    "NEXT_ROLES_ON_SUCCESS",
    "apply_human_action",
    "assess_case",
    "build_awaiting_output",
    "build_line_priorities",
    "find_missing_cfo",
    "resolve_deadline",
]
