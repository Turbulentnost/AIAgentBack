"""Регистрация ролевых агентов ОМТО в реестре платформы.

Каждый агент — тонкий класс над реестром платформы: валидирует вход
(:class:`OmtoAgentRequest`), исполняет свой LangGraph-совместимый граф через
:func:`run_omto_agent` и возвращает :class:`OmtoAgentResult`. Бизнес-логика
целиком живёт в скопированных пакетах ``app.agents.<slug>`` (graph/nodes/state).

Диспетчеризация платформой — по ``agent_id`` (== slug) через ``agent_registry``.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.agents.common.base import BaseAgent
from app.agents.common.registry import agent_registry
from app.agents.omto_role_agents import catalog
from app.agents.omto_role_agents.runner import arun_omto_agent
from app.agents.omto_role_agents.schemas import OmtoAgentRequest, OmtoAgentResult
from app.models.enums import ConfidenceLevel


class _OmtoRoleAgent(BaseAgent):
    """База ролевых агентов ОМТО. Подклассы задают ``agent_id`` (slug)."""

    version = catalog.AGENT_VERSION

    async def run(self, payload: dict) -> OmtoAgentResult:
        try:
            request = OmtoAgentRequest.model_validate(payload)
        except ValidationError as exc:
            return OmtoAgentResult(
                agent_id=self.agent_id,
                status="failed",
                summary="Входные данные ролевого агента ОМТО не прошли проверку.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                correlation_id=str(payload.get("correlation_id") or "unknown"),
                tenant_id=str(payload.get("tenant_id") or "default"),
                task_type=str(payload.get("task_type") or "unknown"),
                role_status="failed",
                wait_reason="validation_error",
                output_data={"validation_errors": exc.errors()},
            )
        spec = catalog.get_spec(self.agent_id)
        if spec is not None and request.task_type not in spec.task_types:
            return OmtoAgentResult(
                agent_id=self.agent_id,
                status="failed",
                summary=(
                    f"Тип задачи «{request.task_type}» не поддерживается агентом "
                    f"«{self.name}». Допустимо: {', '.join(spec.task_types)}."
                ),
                data_confidence=ConfidenceLevel.HIGH,
                requires_human_review=False,
                correlation_id=request.correlation_id,
                tenant_id=request.tenant_id,
                task_type=request.task_type,
                role_status="failed",
                wait_reason="unsupported_task_type",
                output_data={},
            )
        result, _signals = await arun_omto_agent(
            self.agent_id, request, agent_id=self.agent_id
        )
        return result


def _make_agent(slug: str) -> type[_OmtoRoleAgent]:
    spec = catalog.OMTO_AGENTS[slug]

    @agent_registry.register
    class _Agent(_OmtoRoleAgent):
        agent_id = spec.slug
        name = spec.name
        purpose = spec.purpose

    _Agent.__name__ = "".join(part.capitalize() for part in slug.split("_"))
    _Agent.__qualname__ = _Agent.__name__
    return _Agent


ProcurementManagerAgent = _make_agent("procurement_manager_agent")
OmtoHeadAgent = _make_agent("omto_head_agent")
OmtoDeputyAgent = _make_agent("omto_deputy_agent")
KbEngineerAgent = _make_agent("kb_engineer_agent")
SecurityOfficerAgent = _make_agent("security_officer_agent")


__all__ = [
    "KbEngineerAgent",
    "OmtoDeputyAgent",
    "OmtoHeadAgent",
    "ProcurementManagerAgent",
    "SecurityOfficerAgent",
]
