"""Адаптер: исполнение графа ролевого агента ОМТО на боевом LangGraph.

Граф агента (скопированный без изменений) исполняется настоящим движком LangGraph
с checkpointer'ом, что обеспечивает реальные точки HITL: при ``interrupt`` граф
приостанавливается с сохранением состояния и возобновляется после решения человека.

Адаптер:
1. строит состояние из :class:`OmtoAgentRequest` (базовые поля + ``task_payload``);
2. исполняет граф под ``thread_id`` (= correlation_id) с checkpointer'ом;
3. определяет исход: завершён / приостановлен на HITL (waiting_human) / сбой;
4. извлекает измеримые сигналы запуска (реальные данные для KPI);
5. маппит в платформенный контракт :class:`OmtoAgentResult`;
6. умеет возобновлять приостановленный граф решением человека (:func:`resume_omto_agent`).
"""

from __future__ import annotations

import asyncio
import importlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.agents.omto_role_agents.catalog import graph_module_path
from app.agents.omto_role_agents.checkpointer import get_checkpointer
from app.agents.omto_role_agents.runtime_context import bind_runtime, build_runtime
from app.agents.omto_role_agents.schemas import OmtoAgentRequest, OmtoAgentResult
from app.models.enums import ConfidenceLevel
from app.platform_sdk import make_base_state

_CONFIDENCE_MAP = {
    "high": ConfidenceLevel.HIGH,
    "medium": ConfidenceLevel.MEDIUM,
    "low": ConfidenceLevel.LOW,
}

# Статус графа emit_result -> платформенный role_status.
_ROLE_STATUS_MAP = {
    "needs_input": "waiting_human",
    "completed_with_issues": "completed",
    "completed": "completed",
    "waiting_human": "waiting_human",
    "failed": "failed",
}

_CRITICAL_SEVERITIES = {"critical", "high"}


@dataclass
class RunSignals:
    """Измеримые сигналы одного запуска — сохраняются для агрегации KPI."""

    status: str
    role_status: str
    data_confidence: str
    requires_human_review: bool
    total_findings: int = 0
    critical_findings: int = 0
    findings_with_source: int = 0
    source_references: int = 0
    coverage_percent: float | None = None
    verdict_emitted: bool = False
    latency_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def _coerce_coverage(state: dict[str, Any]) -> float | None:
    value = state.get("coverage")
    if value is None:
        return None
    if isinstance(value, (int, float)):
        pct = float(value)
    elif isinstance(value, dict):
        raw = value.get("percent", value.get("coverage", value.get("value")))
        if raw is None:
            return None
        pct = float(raw)
    else:
        return None
    if 0 <= pct <= 1:
        pct *= 100
    return round(pct, 2)


def _verdict_emitted(state: dict[str, Any]) -> bool:
    for key in ("verdict", "conclusion", "risk_level", "kd_changes"):
        if state.get(key):
            return True
    return False


def _extract_signals(state: dict[str, Any], status: str, latency_ms: int) -> RunSignals:
    findings = [f for f in (state.get("findings") or []) if isinstance(f, dict)]
    source_refs = state.get("source_references") or []
    critical = sum(
        1 for f in findings if str(f.get("severity", "")).casefold() in _CRITICAL_SEVERITIES
    )
    with_source = sum(1 for f in findings if f.get("source") or f.get("source_reference"))
    return RunSignals(
        status=status,
        role_status=_ROLE_STATUS_MAP.get(status, "completed"),
        data_confidence=str(state.get("data_confidence") or "medium"),
        requires_human_review=bool(
            state.get("requires_human_review", status == "waiting_human")
        ),
        total_findings=len(findings),
        critical_findings=critical,
        findings_with_source=with_source,
        source_references=len(source_refs) if isinstance(source_refs, list) else 0,
        coverage_percent=_coerce_coverage(state),
        verdict_emitted=_verdict_emitted(state),
        latency_ms=latency_ms,
    )


def _sanitize_output(state: dict[str, Any]) -> dict[str, Any]:
    """Состояние графа как output_data (без служебного шума), JSON-безопасно."""
    filtered = {
        key: value
        for key, value in state.items()
        if key not in {"audit", "__interrupt__"} and not key.startswith("_")
    }
    return json.loads(json.dumps(filtered, ensure_ascii=False, default=str))


def _pending_hitl(compiled: Any, config: dict[str, Any]) -> dict[str, Any] | None:
    """Если граф приостановлен на HITL — возвращает данные точки, иначе None."""
    snapshot = compiled.get_state(config)
    if not snapshot.next:
        return None
    interrupts = getattr(snapshot, "interrupts", None) or []
    value = interrupts[0].value if interrupts else {}
    if isinstance(value, dict):
        return {
            "action": value.get("action"),
            "approver_role": value.get("approver_role"),
            "payload": value.get("payload") or {},
            "resume_node": snapshot.next[0] if snapshot.next else None,
        }
    return {"action": None, "approver_role": None, "payload": {}, "resume_node": None}


def _build_result(
    *,
    agent_id: str,
    request: OmtoAgentRequest,
    state: dict[str, Any],
    latency_ms: int,
    pending: dict[str, Any] | None,
) -> tuple[OmtoAgentResult, RunSignals]:
    if pending is not None:
        status = "waiting_human"
        signals = _extract_signals(state, status, latency_ms)
        signals.requires_human_review = True
        action = pending.get("action") or "подтверждение"
        approver = pending.get("approver_role") or "ответственный"
        output = _sanitize_output(state)
        output["hitl_pending"] = pending
        result = OmtoAgentResult(
            agent_id=agent_id,
            status=status,
            summary=f"Ожидается решение человека: {action} ({approver}).",
            data_confidence=_CONFIDENCE_MAP.get(signals.data_confidence, ConfidenceLevel.MEDIUM),
            requires_human_review=True,
            correlation_id=request.correlation_id,
            tenant_id=request.tenant_id,
            task_type=request.task_type,
            role_status="waiting_human",
            wait_reason=f"HITL: {action} — {approver}",
            output_data=output,
        )
        return result, signals

    emitted = state.get("result") or {}
    status = str(emitted.get("status") or "completed")
    signals = _extract_signals(state, status, latency_ms)
    result = OmtoAgentResult(
        agent_id=agent_id,
        status=status,
        summary=emitted.get("summary"),
        data_confidence=_CONFIDENCE_MAP.get(signals.data_confidence, ConfidenceLevel.MEDIUM),
        requires_human_review=signals.requires_human_review,
        correlation_id=request.correlation_id,
        tenant_id=request.tenant_id,
        task_type=request.task_type,
        role_status=signals.role_status,
        wait_reason=None,
        output_data=_sanitize_output(state),
    )
    return result, signals


def _failure(
    agent_id: str, request: OmtoAgentRequest, exc: Exception, latency_ms: int
) -> tuple[OmtoAgentResult, RunSignals]:
    signals = RunSignals(
        status="failed",
        role_status="failed",
        data_confidence="low",
        requires_human_review=True,
        latency_ms=latency_ms,
    )
    result = OmtoAgentResult(
        agent_id=agent_id,
        status="failed",
        summary=f"Сбой исполнения графа агента: {exc}",
        data_confidence=ConfidenceLevel.LOW,
        requires_human_review=True,
        correlation_id=request.correlation_id,
        tenant_id=request.tenant_id,
        task_type=request.task_type,
        role_status="failed",
        wait_reason=str(exc),
        output_data={"error": str(exc)},
    )
    return result, signals


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def run_omto_agent(
    slug: str,
    request: OmtoAgentRequest,
    *,
    agent_id: str,
) -> tuple[OmtoAgentResult, RunSignals]:
    """Исполняет граф агента под checkpointer'ом. Возвращает (результат, сигналы)."""

    module = importlib.import_module(graph_module_path(slug))
    compiled = module.build_graph(checkpointer=get_checkpointer())
    config = _config(request.correlation_id)
    state = make_base_state(
        request.correlation_id,
        request.tenant_id,
        task_type=request.task_type,
        requested_by=request.requested_by,
    )
    if request.task_payload:
        state.update(request.task_payload)

    started = time.perf_counter()
    try:
        final = compiled.invoke(state, config)
        pending = _pending_hitl(compiled, config)
    except Exception as exc:  # noqa: BLE001 — сбой графа не должен ронять API
        return _failure(agent_id, request, exc, int((time.perf_counter() - started) * 1000))
    latency_ms = int((time.perf_counter() - started) * 1000)
    return _build_result(
        agent_id=agent_id, request=request, state=final, latency_ms=latency_ms, pending=pending
    )


def resume_omto_agent(
    slug: str,
    *,
    thread_id: str,
    decision: dict[str, Any],
    agent_id: str,
    request: OmtoAgentRequest,
) -> tuple[OmtoAgentResult, RunSignals] | None:
    """Возобновляет приостановленный на HITL граф решением человека.

    Возвращает None, если по ``thread_id`` нет приостановленного графа (нечего
    возобновлять — например, состояние утеряно или уже завершено).
    """

    from langgraph.types import Command

    module = importlib.import_module(graph_module_path(slug))
    compiled = module.build_graph(checkpointer=get_checkpointer())
    config = _config(thread_id)

    snapshot = compiled.get_state(config)
    if not snapshot.next:
        return None

    started = time.perf_counter()
    try:
        final = compiled.invoke(Command(resume=decision), config)
        pending = _pending_hitl(compiled, config)
    except Exception as exc:  # noqa: BLE001
        return _failure(agent_id, request, exc, int((time.perf_counter() - started) * 1000))
    latency_ms = int((time.perf_counter() - started) * 1000)
    return _build_result(
        agent_id=agent_id, request=request, state=final, latency_ms=latency_ms, pending=pending
    )


async def arun_omto_agent(
    slug: str,
    request: OmtoAgentRequest,
    *,
    agent_id: str,
) -> tuple[OmtoAgentResult, RunSignals]:
    """Async-обёртка: боевые сервисы платформы + исполнение графа в потоке.

    Собирает :class:`AgentRuntime` (LLM/RAG/1С) на текущем event loop и исполняет
    синхронный граф в отдельном потоке, где узлы через мост зовут async-сервисы.
    """
    runtime = build_runtime(correlation_id=request.correlation_id)

    def _work() -> tuple[OmtoAgentResult, RunSignals]:
        with bind_runtime(runtime):
            return run_omto_agent(slug, request, agent_id=agent_id)

    return await asyncio.to_thread(_work)


async def aresume_omto_agent(
    slug: str,
    *,
    thread_id: str,
    decision: dict[str, Any],
    agent_id: str,
    request: OmtoAgentRequest,
) -> tuple[OmtoAgentResult, RunSignals] | None:
    """Async-обёртка возобновления: боевые сервисы + исполнение в потоке."""
    runtime = build_runtime(correlation_id=thread_id)

    def _work() -> tuple[OmtoAgentResult, RunSignals] | None:
        with bind_runtime(runtime):
            return resume_omto_agent(
                slug,
                thread_id=thread_id,
                decision=decision,
                agent_id=agent_id,
                request=request,
            )

    return await asyncio.to_thread(_work)


__all__ = [
    "RunSignals",
    "aresume_omto_agent",
    "arun_omto_agent",
    "resume_omto_agent",
    "run_omto_agent",
]
