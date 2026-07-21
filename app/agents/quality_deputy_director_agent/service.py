"""ЗДК agent — disposition draft from allowed set, execution conditions, HITL sign."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.agents.procurement_role_agents.schemas import (
    ProcurementRoleAgentRequest,
    ProcurementRoleAgentResult,
)
from app.agents.quality_control_agent.graph import run_quality_pipeline
from app.agents.quality_control_agent.rules_registry import evaluate_scrap_decision
from app.agents.quality_control_agent.schemas import (
    ALLOWED_DISPOSITIONS,
    DISPOSITION_LABELS_RU,
    DispositionCode,
    QualityFinding,
)
from app.agents.quality_control_agent.sla import SLA_ZDK_REVIEW_WH
from app.agents.quality_deputy_director_agent.schemas import QualityDeputyDirectorOutput
from app.models.enums import ConfidenceLevel


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quality_blob(source_data: dict[str, Any], role_context: dict[str, Any]) -> dict[str, Any]:
    nested = source_data.get("quality")
    base = dict(nested) if isinstance(nested, dict) else {}
    for key in (
        "act_ref",
        "scrap_pct",
        "analog_in_nomenclature",
        "disposition",
        "execution_conditions",
        "impact",
        "risks",
    ):
        if key in role_context and key not in base:
            base[key] = role_context[key]
        if key in source_data and key not in base:
            base[key] = source_data[key]
    return base


def _normalize_disposition(raw: Any) -> DispositionCode | None:
    if raw is None:
        return None
    text = str(raw).strip().casefold().replace("ё", "е")
    aliases = {
        "оприходовать": "post_and_use",
        "использовать": "post_and_use",
        "post_and_use": "post_and_use",
        "запретить": "forbid",
        "forbid": "forbid",
        "брак": "forbid",
        "рассортировать": "sort",
        "sort": "sort",
        "вернуть": "return",
        "return": "return",
        "доработка": "rework",
        "rework": "rework",
        "иное": "other",
        "other": "other",
        "комиссия": "commission",
        "commission": "commission",
    }
    for marker, code in aliases.items():
        if marker in text:
            return code  # type: ignore[return-value]
    if text in ALLOWED_DISPOSITIONS:
        return text  # type: ignore[return-value]
    return None


class QualityDeputyDirectorService:
    async def run(self, payload: dict[str, Any], *, agent_id: str) -> ProcurementRoleAgentResult:
        try:
            request = ProcurementRoleAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="failed",
                summary="Входные данные агента ЗДК не прошли проверку.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=str(payload.get("case_id") or "unknown"),
                correlation_id=str(payload.get("correlation_id") or "unknown"),
                role_status="failed",
                output_data={"validation_errors": exc.errors()},
            )

        quality = _quality_blob(request.source_data, request.role_context)
        pipeline = await run_quality_pipeline(
            {
                "case_id": request.case_id,
                "correlation_id": request.correlation_id,
                "source_data": request.source_data,
                "role_context": {
                    **request.role_context,
                    "quality_stage": "zdk",
                },
            }
        )

        scrap = evaluate_scrap_decision(
            _as_float(quality.get("scrap_pct")),
            analog_in_nomenclature=quality.get("analog_in_nomenclature", True),
        )
        proposed = _normalize_disposition(quality.get("disposition")) or _normalize_disposition(
            scrap.get("disposition")
        )
        findings: list[QualityFinding] = []
        within_allowed = True
        if quality.get("disposition") and proposed is None:
            within_allowed = False
            findings.append(
                QualityFinding(
                    field="disposition",
                    rule_id="QC.ZDK.DISPOSITION.ALLOWLIST",
                    source_ref=f"case:{request.case_id}",
                    message="Резолюция вне допустимого перечня",
                    severity="critical",
                    suggested_fix=(
                        "Выбрать: "
                        + ", ".join(DISPOSITION_LABELS_RU[c] for c in ALLOWED_DISPOSITIONS)
                    ),
                    current_value=quality.get("disposition"),
                )
            )

        if proposed is None:
            proposed = "commission"

        conditions = quality.get("execution_conditions")
        if isinstance(conditions, str):
            conditions = [conditions]
        if not isinstance(conditions, list):
            conditions = []
        conditions = [str(c).strip() for c in conditions if str(c).strip()]

        # Default execution conditions by disposition when empty.
        if not conditions:
            defaults = {
                "post_and_use": ["Оприходовать с разрешающим статусом", "Передать на склад"],
                "forbid": ["Изолятор брака", "Блокировка доступного запаса"],
                "sort": ["Рассортировка партии", "Повторный контроль годной части"],
                "return": ["Оформить возврат поставщику", "Претензия"],
                "rework": ["Программа доработки", "Повторное предъявление без старого комментария"],
                "other": ["Указать условия исполнения"],
                "commission": ["Собрать комиссию", "Зафиксировать протокол"],
            }
            conditions = list(defaults.get(proposed, ["Указать условия исполнения"]))

        if any("указать" in c.casefold() for c in conditions) and proposed == "other":
            findings.append(
                QualityFinding(
                    field="execution_conditions",
                    rule_id="QC.ZDK.CONDITIONS",
                    source_ref=f"case:{request.case_id}",
                    message="Условия исполнения не конкретизированы",
                    severity="critical",
                    suggested_fix="Заполнить execution_conditions",
                )
            )

        act_ref = str(quality.get("act_ref") or f"Ф-10-15/{request.case_id}")
        next_status = "rework" if proposed == "rework" else "isolated"
        next_agent = "quality_engineer_agent" if proposed in {"rework", "sort"} else None
        if proposed == "post_and_use":
            next_status = "quality_released"
            next_agent = "quality_engineer_agent"

        now = datetime.now(timezone.utc)
        label = DISPOSITION_LABELS_RU.get(proposed, proposed)
        output = QualityDeputyDirectorOutput(
            disposition=proposed,
            disposition_label=label,
            execution_conditions=conditions,
            act_ref=act_ref,
            within_allowed_list=within_allowed,
            next_status=next_status,
            next_agent=next_agent,
            findings=findings,
            actions=["DRAFT_DISPOSITION", "ROUTE_COPIES"],
            draft_artifacts={
                "resolution_draft": {
                    "act_ref": act_ref,
                    "disposition": proposed,
                    "disposition_label": label,
                    "execution_conditions": conditions,
                    "sla_wh": SLA_ZDK_REVIEW_WH,
                    "scrap_decision": scrap,
                }
            },
            summary=(
                f"Проект резолюции ЗДК по {act_ref}: «{label}». "
                f"SLA рассмотрения ≤ {SLA_ZDK_REVIEW_WH} раб. ч."
            ),
            calculated_at=now,
            quality_control=pipeline.get("quality_control") or {},
        )
        return ProcurementRoleAgentResult(
            agent_id=agent_id,
            status="waiting_human",
            summary=output.summary,
            data_confidence=ConfidenceLevel.HIGH,
            requires_human_review=True,
            case_id=request.case_id,
            correlation_id=request.correlation_id,
            role_status="waiting_human",
            wait_reason="Требуется подпись ЗДК по резолюции (HITL).",
            output_data=output.model_dump(mode="json"),
        )


__all__ = ["QualityDeputyDirectorService"]
