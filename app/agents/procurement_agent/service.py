from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common.base import BaseAgent
from app.agents.common.registry import agent_registry
from app.agents.procurement_agent import config
from app.agents.procurement_agent.graph import build_graph
from app.agents.procurement_agent.schemas import ProcurementAgentRequest, ProcurementAgentResult
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.enums import ConfidenceLevel, ProcurementCaseStatus
from app.models.procurement import ProcurementCase, ProcurementCaseEvent

logger = get_logger(__name__)


def _as_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


@agent_registry.register
class ProcurementAgent(BaseAgent):
    agent_id = config.AGENT_ID
    name = config.AGENT_NAME
    purpose = config.AGENT_PURPOSE
    version = config.AGENT_VERSION
    allowed_tools: list[str] = []

    def __init__(self) -> None:
        self._graph = build_graph()

    async def run(self, payload: dict) -> ProcurementAgentResult:
        try:
            request = ProcurementAgentRequest(**payload)
        except ValidationError as exc:
            logger.warning("procurement.request_invalid", errors=exc.errors())
            return ProcurementAgentResult(
                agent_id=self.agent_id,
                status="failed",
                summary="Запрос закупочного агента не соответствует обязательному контракту.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=True,
                correlation_id=str(payload.get("correlation_id") or payload.get("task_id") or "unknown"),
                case_status=ProcurementCaseStatus.FAILED,
                risks=[{"kind": "validation_error", "details": exc.errors()}],
            )

        external_db = payload.get("db")
        if isinstance(external_db, AsyncSession):
            return await self._run_with_session(request, external_db, commit=False)

        async with AsyncSessionLocal() as db:
            result = await self._run_with_session(request, db, commit=True)
            return result

    async def _run_with_session(
        self,
        request: ProcurementAgentRequest,
        db: AsyncSession,
        *,
        commit: bool,
    ) -> ProcurementAgentResult:
        case, replay = await self._get_or_create_case(db, request)
        if replay and case.latest_result:
            return ProcurementAgentResult.model_validate(case.latest_result)

        await self._append_event(
            db,
            case=case,
            event_type="request_received",
            idempotency_key=request.idempotency_key,
            new_status=ProcurementCaseStatus.NEW.value,
            actor_user_id=_as_uuid(request.user_id),
            actor_role=request.human_role,
            payload={
                "source_type": request.source_type.value,
                "source_1c_ref": request.source_1c_ref,
                "requested_operation": request.requested_operation,
                "autonomy_level": request.autonomy_level,
            },
        )

        final_state = await self._graph.ainvoke(
            {
                **request.model_dump(mode="json"),
                "case_id": str(case.id),
                "source_type": request.source_type.value,
                "findings": [],
                "facts": [],
                "warnings": [],
                "rule_refs": [],
                "artifacts": [],
                "alternatives": [],
                "risks": [],
            }
        )
        missing_fields = final_state.get("missing_fields") or []
        case_status = ProcurementCaseStatus(final_state["case_status"])
        failed = case_status is ProcurementCaseStatus.FAILED
        result = ProcurementAgentResult(
            agent_id=self.agent_id,
            status="failed" if failed else ("completed_with_issues" if missing_fields else "completed"),
            summary=final_state.get("summary"),
            data_confidence=ConfidenceLevel.LOW if failed or missing_fields else ConfidenceLevel.HIGH,
            requires_human_review=bool(missing_fields or final_state.get("required_approval")),
            correlation_id=request.correlation_id,
            case_id=str(case.id),
            case_status=case_status,
            control_point=final_state.get("control_point"),
            facts=final_state.get("facts") or [],
            recommendation=final_state.get("recommendation"),
            alternatives=final_state.get("alternatives") or [],
            risks=final_state.get("risks") or [],
            rule_refs=final_state.get("rule_refs") or [],
            artifacts=final_state.get("artifacts") or [],
            required_approval=final_state.get("required_approval"),
            next_agent=final_state.get("next_agent"),
            next_control_point=final_state.get("next_control_point"),
            missing_fields=missing_fields,
        )

        case.status = case_status.value
        case.control_point = final_state.get("control_point")
        case.current_agent_id = final_state.get("next_agent") or self.agent_id
        case.latest_result = result.model_dump(mode="json")
        case.error_message = result.summary if failed else None
        await self._append_event(
            db,
            case=case,
            event_type="level_zero_assessment_completed",
            idempotency_key=f"{request.idempotency_key[:247]}:result",
            previous_status=ProcurementCaseStatus.NEW.value,
            new_status=case_status.value,
            actor_user_id=_as_uuid(request.user_id),
            actor_role=request.human_role,
            rule_refs=result.rule_refs,
            payload=result.model_dump(mode="json"),
        )
        if commit:
            await db.commit()
        else:
            await db.flush()
        return result

    async def _get_or_create_case(
        self,
        db: AsyncSession,
        request: ProcurementAgentRequest,
    ) -> tuple[ProcurementCase, bool]:
        by_key = await db.scalar(
            select(ProcurementCase).where(ProcurementCase.idempotency_key == request.idempotency_key)
        )
        if by_key is not None:
            if by_key.correlation_id != request.correlation_id:
                raise ValueError("Idempotency key is already bound to another correlation_id")
            return by_key, True

        by_correlation = await db.scalar(
            select(ProcurementCase).where(ProcurementCase.correlation_id == request.correlation_id)
        )
        if by_correlation is not None:
            return by_correlation, False

        case = ProcurementCase(
            correlation_id=request.correlation_id,
            source_type=request.source_type.value,
            source_1c_ref=request.source_1c_ref,
            status=ProcurementCaseStatus.NEW.value,
            control_point="KT1",
            autonomy_level=request.autonomy_level,
            current_agent_id=self.agent_id,
            current_human_role=request.human_role,
            requested_operation=request.requested_operation,
            idempotency_key=request.idempotency_key,
            graph_version=config.GRAPH_VERSION,
            case_metadata={"deadline": request.deadline.isoformat() if request.deadline else None},
            created_by_user_id=_as_uuid(request.user_id),
        )
        db.add(case)
        await db.flush()
        return case, False

    async def _append_event(
        self,
        db: AsyncSession,
        *,
        case: ProcurementCase,
        event_type: str,
        idempotency_key: str,
        new_status: str,
        previous_status: str | None = None,
        actor_user_id: uuid.UUID | None = None,
        actor_role: str | None = None,
        rule_refs: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        exists = await db.scalar(
            select(ProcurementCaseEvent.id).where(
                ProcurementCaseEvent.case_id == case.id,
                ProcurementCaseEvent.idempotency_key == idempotency_key,
            )
        )
        if exists is not None:
            return
        db.add(
            ProcurementCaseEvent(
                case_id=case.id,
                correlation_id=case.correlation_id,
                event_type=event_type,
                agent_id=self.agent_id,
                actor_user_id=actor_user_id,
                actor_role=actor_role,
                previous_status=previous_status,
                new_status=new_status,
                idempotency_key=idempotency_key,
                rule_refs=rule_refs,
                payload=payload,
            )
        )


__all__ = ["ProcurementAgent"]
