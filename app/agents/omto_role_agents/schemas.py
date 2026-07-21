from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.common.schemas import AgentResult

# Статус результата ролевого агента ОМТО (маппинг из статуса графа emit_result).
OmtoRoleStatus = Literal["waiting_human", "waiting_external", "completed", "failed"]


class OmtoAgentRequest(BaseModel):
    """Входной контракт ролевого агента ОМТО.

    В отличие от закупочного оркестратора (управляется ``source_type`` документа 1С),
    агенты ОМТО управляются ``task_type``. Предметные поля задачи передаются в
    ``task_payload`` и накладываются на состояние графа.
    """

    correlation_id: str = Field(..., min_length=1, max_length=128)
    tenant_id: str = Field(default="default", max_length=128)
    task_type: str = Field(..., min_length=1, max_length=128)
    requested_by: str = Field(default="orchestrator", max_length=128)
    caller_agent_id: str = Field(default="omto_orchestrator", max_length=128)
    task_payload: dict[str, Any] = Field(default_factory=dict)


class OmtoAgentResult(AgentResult):
    """Результат ролевого агента ОМТО.

    Наследует платформенный ``AgentResult`` (agent_id/status/summary/findings/
    data_confidence/requires_human_review) и добавляет сквозные поля кейса и
    полный вывод графа для дашбордов и последующей сверки.
    """

    correlation_id: str
    tenant_id: str = "default"
    task_type: str
    role_status: OmtoRoleStatus
    wait_reason: str | None = None
    output_data: dict[str, Any] = Field(default_factory=dict)


__all__ = ["OmtoAgentRequest", "OmtoAgentResult", "OmtoRoleStatus"]
