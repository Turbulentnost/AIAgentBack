from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

import app.tools  # noqa: F401  # ensure all tools are registered in any process that runs agents
from app.agents.builder.meta_tools import BUILDER_META_TOOLS
from app.agents.builder.preview_tool_params import infer_preview_tool_params
from app.tools.executor import ToolExecutor
from app.tools.registry import tool_registry
from app.tools.schemas import ToolContext
from app.tools.system_tools import resolve_current_date
from app.core.config import settings
from app.core.logging import get_logger
from app.llm.gateway import llm_gateway

logger = get_logger(__name__)

MAX_ITERATIONS = 6

# Builder/meta tools that must never run inside a consultant sandbox execution.
EXCLUDED_RUNTIME_TOOLS = BUILDER_META_TOOLS

# Callback signatures used to persist a live trace.
StepStart = Callable[[dict[str, Any]], Awaitable[Any]]
StepFinish = Callable[[Any, dict[str, Any]], Awaitable[None]]


class ConsultantRunResult:
    def __init__(
        self,
        *,
        final_answer: str,
        steps: list[dict[str, Any]],
        stats: dict[str, Any],
        executed_graph: dict[str, Any],
        used_fallback: bool,
    ) -> None:
        self.final_answer = final_answer
        self.steps = steps
        self.stats = stats
        self.executed_graph = executed_graph
        self.used_fallback = used_fallback


def _resolve_runtime_tools(blueprint: dict[str, Any]) -> list[str]:
    blueprint_tools = [t for t in (blueprint.get("tools") or []) if isinstance(t, str)]
    resolved: list[str] = []
    for name in blueprint_tools:
        if name in EXCLUDED_RUNTIME_TOOLS:
            continue
        definition = tool_registry.get(name)
        if definition is None or not definition.implemented:
            continue
        if name not in resolved:
            resolved.append(name)
    if "get_current_date" not in resolved and tool_registry.get("get_current_date"):
        resolved.insert(0, "get_current_date")
    return resolved


def _openai_tools_schema(tool_names: list[str]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for name in tool_names:
        definition = tool_registry.get(name)
        if definition is None:
            continue
        parameters = definition.input_schema or {"type": "object", "properties": {}}
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": definition.agent_description or definition.description,
                    "parameters": parameters,
                },
            }
        )
    return tools


def _build_system_prompt(blueprint: dict[str, Any]) -> str:
    prompts = blueprint.get("prompts") or {}
    base = prompts.get("system") or "Ты агент-консультант."
    card = blueprint.get("agent_card") or {}
    purpose = card.get("purpose") or ""
    current = resolve_current_date()
    return (
        f"{base}\n\n"
        f"Назначение агента: {purpose}\n"
        f"Сегодня: {current.date_ru} ({current.weekday_ru}), {current.date_iso}.\n"
        "Ты выполняешь реальный пробный запуск. Используй доступные инструменты, чтобы получить "
        "актуальные данные, а затем сформируй полный структурированный ответ пользователю. "
        "Не выдумывай факты и не используй плейсхолдеры — опирайся только на результаты инструментов. "
        "Когда данных достаточно, перестань вызывать инструменты и верни финальный ответ текстом."
    )


def _summarize_result(tool_name: str, params: dict[str, Any], result: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    if isinstance(params, dict) and params.get("url"):
        summary["url"] = params["url"]
    if isinstance(result, dict):
        text = result.get("text") or result.get("html")
        if isinstance(text, str):
            summary["bytes"] = len(text.encode("utf-8"))
            summary["preview"] = text.strip()[:280]
        if result.get("title"):
            summary["title"] = result["title"]
        items = result.get("results")
        if isinstance(items, list):
            summary["results_count"] = len(items)
        kb = result.get("items")
        if isinstance(kb, list):
            summary["items_count"] = len(kb)
        if result.get("date_ru"):
            summary["date"] = result["date_ru"]
        if result.get("error_message"):
            summary["error"] = result["error_message"]
        if result.get("status"):
            summary["status"] = result["status"]
    if not summary:
        summary["note"] = "результат получен"
    return summary


def _label_for_tool(tool_name: str) -> str:
    definition = tool_registry.get(tool_name)
    if definition is not None and definition.description:
        return f"{tool_name}: {definition.description}"
    return tool_name


class ConsultantRunner:
    def __init__(self) -> None:
        self._executor = ToolExecutor()

    async def execute(
        self,
        *,
        blueprint: dict[str, Any],
        test_query: str,
        db: Any,
        user: Any,
        on_step_start: StepStart | None = None,
        on_step_finish: StepFinish | None = None,
    ) -> ConsultantRunResult:
        tool_names = _resolve_runtime_tools(blueprint)
        context = ToolContext.model_construct(
            db=db,
            user=user,
            agent_id=None,
            task_id=None,
            allow_open_web=settings.BROWSER_SANDBOX_OPEN_WEB,
        )
        steps: list[dict[str, Any]] = []

        async def run_tool(order: int, capability: str | None, tool_name: str, params: dict[str, Any]) -> Any:
            step_info = {
                "order_index": order,
                "title": _label_for_tool(tool_name),
                "capability": capability,
                "tool_name": tool_name,
                "request": params,
                "status": "running",
            }
            handle = None
            if on_step_start is not None:
                handle = await on_step_start(step_info)
            started = perf_counter()
            status = "completed"
            error: str | None = None
            result: Any = None
            summary: dict[str, Any] = {}
            try:
                result = await self._executor.invoke(
                    tool_name=tool_name,
                    params=params,
                    context=context,
                    allowed_tools=tool_names,
                )
                summary = _summarize_result(tool_name, params, result)
            except Exception as exc:  # noqa: BLE001
                status = "failed"
                error = str(exc)
                summary = {"error": error}
                logger.warning("sandbox.tool_failed", tool=tool_name, error=error)
            duration_ms = int((perf_counter() - started) * 1000)
            record = {
                **step_info,
                "status": status,
                "result_summary": summary,
                "duration_ms": duration_ms,
                "error_message": error,
            }
            steps.append(record)
            if on_step_finish is not None:
                await on_step_finish(handle, record)
            return result

        final_answer, used_fallback = await self._run_llm_loop(
            blueprint=blueprint,
            test_query=test_query,
            tool_names=tool_names,
            run_tool=run_tool,
        )

        stats = self._compute_stats(steps)
        executed_graph = self._build_executed_graph(steps)
        return ConsultantRunResult(
            final_answer=final_answer,
            steps=steps,
            stats=stats,
            executed_graph=executed_graph,
            used_fallback=used_fallback,
        )

    async def _run_llm_loop(
        self,
        *,
        blueprint: dict[str, Any],
        test_query: str,
        tool_names: list[str],
        run_tool: Callable[[int, str | None, str, dict[str, Any]], Awaitable[Any]],
    ) -> tuple[str, bool]:
        tools_schema = _openai_tools_schema(tool_names)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _build_system_prompt(blueprint)},
            {"role": "user", "content": test_query},
        ]
        order = 0
        any_tool_called = False
        final_answer = ""

        for _ in range(MAX_ITERATIONS):
            try:
                response = await llm_gateway.chat(
                    messages,
                    tools=tools_schema,
                    tool_choice="auto",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("sandbox.llm_failed", error=str(exc))
                break

            choice = (response.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                final_answer = (message.get("content") or "").strip()
                break

            any_tool_called = True
            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls:
                fn = call.get("function") or {}
                tool_name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    params = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    params = {}
                order += 1
                result = await run_tool(order, None, tool_name, params)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "name": tool_name,
                        "content": json.dumps(result, ensure_ascii=False, default=str)[:6000],
                    }
                )

        if not any_tool_called and tool_names:
            return await self._deterministic_fallback(
                blueprint=blueprint,
                test_query=test_query,
                tool_names=tool_names,
                run_tool=run_tool,
                order_start=order,
            )

        if not final_answer:
            final_answer = await self._format_final_answer(blueprint, test_query, [])
        return final_answer, False

    async def _deterministic_fallback(
        self,
        *,
        blueprint: dict[str, Any],
        test_query: str,
        tool_names: list[str],
        run_tool: Callable[[int, str | None, str, dict[str, Any]], Awaitable[Any]],
        order_start: int,
    ) -> tuple[str, bool]:
        requirements = {"required_elements": [], "conversation": [{"content": test_query}]}
        collected: list[dict[str, Any]] = []
        order = order_start
        for tool_name in tool_names:
            definition = tool_registry.get(tool_name)
            params = infer_preview_tool_params(tool_name, test_query, requirements)
            if params is None and definition is not None and definition.preview_default_params:
                params = dict(definition.preview_default_params)
            if params is None:
                continue
            order += 1
            result = await run_tool(order, None, tool_name, params)
            collected.append({"tool": tool_name, "result": result})

        final_answer = await self._format_final_answer(blueprint, test_query, collected)
        return final_answer, True

    async def _format_final_answer(
        self,
        blueprint: dict[str, Any],
        test_query: str,
        collected: list[dict[str, Any]],
    ) -> str:
        try:
            response = await llm_gateway.chat(
                [
                    {"role": "system", "content": _build_system_prompt(blueprint)},
                    {
                        "role": "user",
                        "content": (
                            f"Запрос: {test_query}\n\n"
                            f"Данные инструментов (JSON): "
                            f"{json.dumps(collected, ensure_ascii=False, default=str)[:6000]}\n\n"
                            "Сформируй финальный структурированный ответ пользователю только на основе этих данных."
                        ),
                    },
                ]
            )
            choice = (response.get("choices") or [{}])[0]
            content = ((choice.get("message") or {}).get("content") or "").strip()
            if content:
                return content
        except Exception as exc:  # noqa: BLE001
            logger.warning("sandbox.format_answer_failed", error=str(exc))
        if collected:
            return "Получены данные инструментов, но не удалось сформировать текстовый ответ."
        return "Пробный запуск завершён, но инструменты не вернули данных для ответа."

    @staticmethod
    def _compute_stats(steps: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(steps)
        success = sum(1 for s in steps if s.get("status") == "completed")
        errors = sum(1 for s in steps if s.get("status") == "failed")
        durations = [s.get("duration_ms") or 0 for s in steps]
        avg_ms = int(sum(durations) / total) if total else 0
        return {
            "total_steps": total,
            "success_steps": success,
            "error_steps": errors,
            "avg_duration_ms": avg_ms,
            "total_duration_ms": sum(durations),
        }

    @staticmethod
    def _build_executed_graph(steps: list[dict[str, Any]]) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = [
            {"id": "start", "label": "Старт", "type": "start", "status": "completed"}
        ]
        edges: list[dict[str, str]] = []
        prev = "start"
        for index, step in enumerate(steps, start=1):
            node_id = f"step_{index}"
            nodes.append(
                {
                    "id": node_id,
                    "label": step.get("tool_name") or step.get("title") or f"Шаг {index}",
                    "type": "step",
                    "status": step.get("status"),
                    "capability": step.get("capability"),
                    "goal": step.get("title"),
                }
            )
            edges.append({"source": prev, "target": node_id})
            prev = node_id
        nodes.append({"id": "end", "label": "Завершение", "type": "end", "status": "completed"})
        edges.append({"source": prev, "target": "end"})
        return {"nodes": nodes, "edges": edges}


consultant_runner = ConsultantRunner()
