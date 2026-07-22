"""Quality engineer — doc check, program/sample, decision drafts (no physical inspection)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.agents.procurement_role_agents.schemas import (
    ProcurementRoleAgentRequest,
    ProcurementRoleAgentResult,
)
from app.agents.quality_control_agent.graph import run_quality_pipeline
from app.agents.quality_control_agent.rules_registry import (
    build_sample_rule,
    evaluate_scrap_decision,
    normalize_category,
)
from app.agents.quality_control_agent.schemas import QualityFinding, QualitySampleRule
from app.agents.quality_engineer_agent.schemas import QualityEngineerOutput
from app.models.enums import ConfidenceLevel


def _quality_blob(source_data: dict[str, Any], role_context: dict[str, Any]) -> dict[str, Any]:
    nested = source_data.get("quality")
    base = dict(nested) if isinstance(nested, dict) else {}
    for key in (
        "item_group",
        "category",
        "present_docs",
        "documents",
        "scrap_pct",
        "analog_in_nomenclature",
        "inspection_complete",
        "measured_results",
        "instrument_refs",
        "evidence_refs",
        "fitness_status",
        "lot_qty",
        "quantity",
        "presentation_ref",
        "nomenclature_ref",
        "supplier_ref",
        "supplier_quality_rating",
    ):
        if key in role_context and key not in base:
            base[key] = role_context[key]
        if key in source_data and key not in base:
            base[key] = source_data[key]
    return base


class QualityEngineerService:
    async def run(self, payload: dict[str, Any], *, agent_id: str) -> ProcurementRoleAgentResult:
        try:
            request = ProcurementRoleAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="failed",
                summary="Входные данные агента инженера по качеству не прошли проверку.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=str(payload.get("case_id") or "unknown"),
                correlation_id=str(payload.get("correlation_id") or "unknown"),
                role_status="failed",
                output_data={"validation_errors": exc.errors()},
            )

        quality = _quality_blob(request.source_data, request.role_context)
        stage = str(
            request.role_context.get("quality_stage")
            or quality.get("quality_stage")
            or "assigned"
        ).lower()
        if stage in {"queued", "quality_queued"}:
            stage = "assigned"

        pipeline = await run_quality_pipeline(
            {
                "case_id": request.case_id,
                "correlation_id": request.correlation_id,
                "source_data": request.source_data,
                "role_context": {**request.role_context, "quality_stage": stage},
            }
        )

        category = normalize_category(
            str(quality.get("item_group") or quality.get("category") or pipeline.get("category"))
        )
        findings = [
            QualityFinding.model_validate(item)
            for item in (pipeline.get("doc_findings") or [])
            if isinstance(item, dict)
        ]
        sample = QualitySampleRule.model_validate(
            pipeline.get("sample_rule")
            or build_sample_rule(category).model_dump(mode="json")
        )
        # Дозаполнить контекст поставки, если входные данные богаче пайплайна.
        if (
            sample.lot_qty is None
            or sample.presentation_ref is None
            or sample.sample_size is None
        ):
            sample = build_sample_rule(
                category,
                lot_qty=quality.get("lot_qty") or quality.get("quantity") or sample.lot_qty,
                analog_in_nomenclature=quality.get("analog_in_nomenclature", True),
                presentation_ref=quality.get("presentation_ref") or sample.presentation_ref,
                nomenclature_ref=quality.get("nomenclature_ref") or sample.nomenclature_ref,
                supplier_ref=quality.get("supplier_ref") or sample.supplier_ref,
                supplier_quality_rating=(
                    quality.get("supplier_quality_rating") or sample.supplier_quality_rating
                ),
                require_second_sample=sample.require_second_sample,
            )
        now = datetime.now(timezone.utc)
        docs_ok = not findings

        # Physical inspection is always human — agent only checks completeness.
        inspection_complete = bool(quality.get("inspection_complete"))
        measured = quality.get("measured_results") or []
        instruments = quality.get("instrument_refs") or []
        evidence = quality.get("evidence_refs") or []
        fitness = str(quality.get("fitness_status") or "pending").lower()

        if not docs_ok or stage in {"assigned", "quality_assigned", "doc_check", "quality_doc_check"}:
            if not docs_ok:
                output = QualityEngineerOutput(
                    stage="doc_check",
                    category=category,
                    mandatory_docs_ok=False,
                    sample_rule=sample,
                    fitness_status="pending",
                    next_status="quality_doc_check",
                    next_agent="quality_engineer_agent",
                    findings=findings,
                    actions=["DOC_CHECK", "REQUEST_DOCS"],
                    draft_artifacts={"checklist": pipeline.get("mandatory_documents") or []},
                    summary=f"Документарная проверка: замечаний {len(findings)} (SLA ≤ 0.5 ч).",
                    calculated_at=now,
                    quality_control=pipeline.get("quality_control") or {},
                )
                return self._hitl(
                    agent_id,
                    request,
                    output,
                    "Требуется комплект документов по категории ТМЦ и проверка оригиналов.",
                )
            output = QualityEngineerOutput(
                stage="program",
                category=category,
                mandatory_docs_ok=True,
                sample_rule=sample,
                fitness_status="pending",
                next_status="quality_inspection",
                next_agent="quality_engineer_agent",
                findings=[],
                actions=["BUILD_PROGRAM", "SAMPLE_RULE"],
                draft_artifacts={
                    "control_program": sample.model_dump(mode="json"),
                    "note": "Физический контроль выполняет человек; агент не подменяет измерения.",
                },
                summary=f"Программа контроля и выборка готовы ({sample.rule_id}).",
                calculated_at=now,
                quality_control=pipeline.get("quality_control") or {},
            )
            return self._hitl(
                agent_id,
                request,
                output,
                "Подтвердите программу контроля и выполните физический осмотр.",
            )

        if not inspection_complete or stage in {"inspection", "quality_inspection"}:
            missing: list[QualityFinding] = []
            if not measured:
                missing.append(
                    QualityFinding(
                        field="measured_results",
                        rule_id="QC.QI.RESULTS",
                        source_ref=f"case:{request.case_id}",
                        message="Не зафиксированы результаты измерений / испытаний",
                        severity="critical",
                        suggested_fix="Внести measured_results после осмотра человеком",
                    )
                )
            if not instruments:
                missing.append(
                    QualityFinding(
                        field="instrument_refs",
                        rule_id="QC.QI.INSTRUMENTS",
                        source_ref=f"case:{request.case_id}",
                        message="Не указаны средства измерения",
                        severity="warning",
                        suggested_fix="Указать instrument_refs",
                    )
                )
            if not evidence:
                missing.append(
                    QualityFinding(
                        field="evidence_refs",
                        rule_id="QC.QI.EVIDENCE",
                        source_ref=f"case:{request.case_id}",
                        message="Нет ссылок на доказательства (фото/протоколы)",
                        severity="warning",
                        suggested_fix="Приложить evidence_refs",
                    )
                )
            output = QualityEngineerOutput(
                stage="inspection",
                category=category,
                mandatory_docs_ok=True,
                sample_rule=sample,
                fitness_status="pending",
                next_status="quality_inspection",
                next_agent="quality_engineer_agent",
                findings=missing,
                actions=["RECORD_INSPECTION"],
                draft_artifacts={
                    "measured_results": measured,
                    "instrument_refs": instruments,
                    "evidence_refs": evidence,
                },
                summary="Ожидается запись результатов физического контроля человеком.",
                calculated_at=now,
                quality_control=pipeline.get("quality_control") or {},
            )
            return self._hitl(
                agent_id,
                request,
                output,
                "Зафиксируйте результаты осмотра, средства измерения и доказательства.",
            )

        scrap = evaluate_scrap_decision(
            _as_float(quality.get("scrap_pct")),
            analog_in_nomenclature=quality.get("analog_in_nomenclature", True),
        )
        if fitness in {"doubtful", "unidentified"}:
            fitness = "doubtful"
        if fitness == "pending":
            if scrap.get("disposition") in {"forbid", "commission"}:
                fitness = "unfit"
            elif scrap.get("disposition") == "post_and_use":
                fitness = "fit"

        if fitness in {"unfit", "doubtful"} or scrap.get("require_zdk"):
            act_ref = f"Ф-10-15/{request.case_id}"
            output = QualityEngineerOutput(
                stage="nc_act",
                category=category,
                mandatory_docs_ok=True,
                sample_rule=sample,
                fitness_status="doubtful" if fitness == "doubtful" else "unfit",
                act_ref=act_ref,
                next_status="nonconformity",
                next_agent="otk_head_agent",
                findings=findings,
                actions=["DRAFT_F10_15", "ISOLATOR_TASK"],
                draft_artifacts={
                    "act_draft": {
                        "form": "Ф-10-15",
                        "act_ref": act_ref,
                        "scrap_decision": scrap,
                        "evidence_refs": evidence,
                    },
                    "note": "Сомнительный/неидентифицированный статус = несоответствие.",
                },
                summary=f"Проект акта {act_ref}. Передача начальнику ОТК.",
                calculated_at=now,
                quality_control=pipeline.get("quality_control") or {},
            )
            return self._hitl(
                agent_id,
                request,
                output,
                "Требуется подпись инженера по акту несоответствия.",
            )

        label_ref = f"Ф-10-38/{request.case_id}"
        output = QualityEngineerOutput(
            stage="release",
            category=category,
            mandatory_docs_ok=True,
            sample_rule=sample,
            fitness_status="fit",
            label_ref=label_ref,
            next_status="quality_released",
            next_agent=None,
            findings=[],
            actions=["DRAFT_F10_38", "QUALITY_RELEASED"],
            draft_artifacts={
                "label_draft": {"form": "Ф-10-38", "label_ref": label_ref},
                "releasing_status": "fit",
            },
            summary=f"Проект разрешающего статуса и ярлыка {label_ref}.",
            calculated_at=now,
            quality_control=pipeline.get("quality_control") or {},
        )
        return self._hitl(
            agent_id,
            request,
            output,
            "Требуется подпись инженера для разрешающего статуса.",
        )

    def _hitl(
        self,
        agent_id: str,
        request: ProcurementRoleAgentRequest,
        output: QualityEngineerOutput,
        wait_reason: str,
    ) -> ProcurementRoleAgentResult:
        return ProcurementRoleAgentResult(
            agent_id=agent_id,
            status="waiting_human",
            summary=output.summary,
            data_confidence=ConfidenceLevel.HIGH,
            requires_human_review=True,
            case_id=request.case_id,
            correlation_id=request.correlation_id,
            role_status="waiting_human",
            wait_reason=wait_reason,
            output_data=output.model_dump(mode="json"),
        )


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["QualityEngineerService"]
