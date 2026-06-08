from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.builder.llm import (
    BuilderLLMError,
    append_conversation,
    apply_heuristic_element_answers,
    builder_llm,
    finalize_requirements,
    merge_required_elements,
    merge_requirements,
    pending_questions_for_elements,
    summarize_filled_elements,
)
from app.agents.builder.state import AgentBuilderState
from app.agents.builder.tools import (
    blueprint_from_llm,
    build_default_blueprint,
    default_plan_steps,
    list_available_tools_catalog,
    render_workflow_graph,
)
from app.agents.builder.preview_runner import run_agent_preview
from app.agents.builder.validators import validate_agent_blueprint, validate_required_elements
from app.core.logging import get_logger
from app.models.enums import AgentBuilderSessionStatus

logger = get_logger(__name__)


def _conversation(state: AgentBuilderState) -> list[dict[str, str]]:
    requirements = state.get("collected_requirements") or {}
    stored = requirements.get("conversation")
    if isinstance(stored, list):
        return [item for item in stored if isinstance(item, dict)]
    return list(state.get("conversation") or [])


def _store_conversation(requirements: dict[str, Any], conversation: list[dict[str, str]]) -> dict[str, Any]:
    updated = dict(requirements)
    updated["conversation"] = conversation
    return updated


async def understand_goal(state: AgentBuilderState) -> dict:
    logger.info("builder.understand_goal", session_id=state.get("session_id"))
    goal = (state.get("goal") or "").strip()
    if not goal:
        return {
            "status": AgentBuilderSessionStatus.FAILED.value,
            "assistant_messages": ["Опишите задачу агента, которого нужно спроектировать."],
            "requires_user_input": True,
        }
    requirements = dict(state.get("collected_requirements") or {})
    requirements.setdefault("goal", goal)
    conversation = _conversation(state)
    if not conversation:
        conversation = append_conversation(conversation, "user", goal)
        requirements = _store_conversation(requirements, conversation)
    return {
        "current_stage": "understand_goal",
        "collected_requirements": requirements,
        "conversation": conversation,
        "status": AgentBuilderSessionStatus.PLANNING.value,
    }


async def ask_clarifying_questions(state: AgentBuilderState) -> dict:
    logger.info("builder.ask_clarifying_questions")
    requirements = dict(state.get("collected_requirements") or {})
    conversation = _conversation(state)
    goal = state.get("goal", "")

    user_message = (state.get("user_message") or "").strip()
    if user_message:
        conversation = append_conversation(conversation, "user", user_message)

    existing_elements = requirements.get("required_elements") or []
    has_pending = any(
        item.get("required", True) and item.get("status") != "filled" and not item.get("value")
        for item in existing_elements
        if isinstance(item, dict)
    )
    if user_message and (has_pending or not existing_elements):
        try:
            extracted = await builder_llm.extract_element_answers(
                goal=goal,
                user_message=user_message,
                required_elements=existing_elements,
                conversation=conversation,
            )
            requirements = merge_requirements(requirements, extracted.extracted_requirements)
            requirements["required_elements"] = merge_required_elements(
                existing_elements,
                [item.model_dump() for item in extracted.elements],
            )
        except BuilderLLMError as exc:
            logger.warning("builder.extract_answers_failed", error=str(exc))

    if user_message:
        requirements["required_elements"] = apply_heuristic_element_answers(
            user_message,
            requirements.get("required_elements") or [],
        )

    try:
        llm_result = await builder_llm.clarify(
            goal=goal,
            conversation=conversation,
            requirements=requirements,
        )
    except BuilderLLMError as exc:
        return _llm_failure(str(exc))

    requirements = merge_requirements(requirements, llm_result.extracted_requirements)
    incoming_elements = [item.model_dump() for item in llm_result.required_elements]
    requirements["required_elements"] = merge_required_elements(
        requirements.get("required_elements") or [],
        incoming_elements,
    )

    elements = requirements.get("required_elements") or []
    pending_questions = pending_questions_for_elements(elements)
    if not pending_questions:
        pending_questions = [q for q in (llm_result.clarifying_questions or []) if q.strip()]
    requirements["pending_questions"] = pending_questions
    requirements = finalize_requirements(requirements)
    requirements_validation = requirements["requirements_validation"]
    pending_questions = requirements.get("pending_questions") or []

    ready_to_plan = requirements_validation["valid"]
    filled_summary = summarize_filled_elements(elements)
    if ready_to_plan:
        assistant_text = (
            f"{filled_summary} Все обязательные данные собраны. Перехожу к планированию."
            if filled_summary
            else "Все обязательные данные собраны. Перехожу к планированию."
        )
    else:
        assistant_text = llm_result.assistant_message
        if filled_summary and filled_summary not in assistant_text:
            assistant_text = f"{filled_summary} {assistant_text}".strip()
        if requirements_validation.get("missing"):
            assistant_text = (
                f"{assistant_text} Не заполнено: {', '.join(requirements_validation['missing'])}."
            ).strip()

    conversation = append_conversation(conversation, "assistant", assistant_text)
    requirements = _store_conversation(requirements, conversation)

    if ready_to_plan:
        return {
            "current_stage": "ask_clarifying_questions",
            "collected_requirements": requirements,
            "conversation": conversation,
            "clarifying_questions": [],
            "assistant_messages": [assistant_text],
            "requires_user_input": False,
            "status": AgentBuilderSessionStatus.PLANNING.value,
        }

    return {
        "current_stage": "ask_clarifying_questions",
        "collected_requirements": requirements,
        "conversation": conversation,
        "clarifying_questions": pending_questions,
        "assistant_messages": [assistant_text],
        "requires_user_input": True,
        "status": AgentBuilderSessionStatus.NEEDS_CLARIFICATION.value,
    }


async def create_plan(state: AgentBuilderState) -> dict:
    logger.info("builder.create_plan")
    service = state.get("service")
    requirements = state.get("collected_requirements") or {}
    goal = state.get("goal", "")
    requirements_validation = validate_required_elements(requirements)
    if not requirements_validation["valid"]:
        missing = ", ".join(requirements_validation.get("missing") or [])
        return {
            "current_stage": "ask_clarifying_questions",
            "collected_requirements": {
                **requirements,
                "requirements_validation": requirements_validation,
            },
            "clarifying_questions": requirements.get("pending_questions") or [],
            "assistant_messages": [f"Сначала заполните все обязательные элементы: {missing}"],
            "requires_user_input": True,
            "status": AgentBuilderSessionStatus.NEEDS_CLARIFICATION.value,
        }

    try:
        llm_plan = await builder_llm.generate_plan(goal=goal, requirements=requirements)
        steps = [{"title": step.title, "description": step.description} for step in llm_plan.steps]
        if not steps:
            raise BuilderLLMError("Модель вернула пустой план")
        summary = llm_plan.summary
    except BuilderLLMError as exc:
        return _llm_failure(str(exc))

    if service is not None:
        await service.save_plan(state["session_id"], steps, current_user=state.get("current_user"))

    assistant_messages = [summary] if summary else [f"Сформирован план из {len(steps)} шагов."]
    requirements = dict(state.get("collected_requirements") or {})
    requirements = finalize_requirements(requirements)
    conversation = _conversation(state)
    for text in assistant_messages:
        conversation = append_conversation(conversation, "assistant", text)
    requirements = _store_conversation(requirements, conversation)
    return {
        "current_stage": "create_plan",
        "plan_steps": steps,
        "current_step_index": 0,
        "collected_requirements": requirements,
        "conversation": conversation,
        "clarifying_questions": [],
        "assistant_messages": assistant_messages,
        "requires_user_input": False,
        "status": AgentBuilderSessionStatus.EXECUTING.value,
    }


async def execute_plan_step(state: AgentBuilderState) -> dict:
    logger.info("builder.execute_plan_step")
    service = state.get("service")
    index = state.get("current_step_index", 0)
    steps = state.get("plan_steps") or default_plan_steps()
    if index >= len(steps):
        return {"current_stage": "execute_plan_step", "status": AgentBuilderSessionStatus.GENERATED.value}

    step = steps[index]
    requirements = dict(state.get("collected_requirements") or {})
    result: dict[str, Any] = {
        "step": step["title"],
        "description": step.get("description"),
        "status": "completed",
    }

    title_lower = step["title"].lower()
    description_lower = (step.get("description") or "").lower()
    if any(word in title_lower or word in description_lower for word in ("инструмент", "tool")):
        catalog = list_available_tools_catalog()
        implemented = [item["name"] for item in catalog if item["implemented"]]
        result["tools"] = implemented[:8]
        requirements["recommended_tools"] = result["tools"]
    if any(word in title_lower or word in description_lower for word in ("workflow", "процесс", "этап", "граф")):
        workflow_steps = requirements.get("workflow_hints") or [
            "Получение входных данных",
            "Обработка задачи",
            "Формирование результата",
        ]
        if isinstance(workflow_steps, str):
            workflow_steps = [workflow_steps]
        result["workflow_graph"] = render_workflow_graph(workflow_steps)

    if service is not None:
        await service.complete_plan_step(
            state["session_id"],
            step_order=index,
            result=result,
            current_user=state.get("current_user"),
        )

    return {
        "current_stage": "execute_plan_step",
        "current_step_index": index + 1,
        "collected_requirements": requirements,
        "workflow_graph": result.get("workflow_graph") or state.get("workflow_graph"),
    }


async def collect_requirements(state: AgentBuilderState) -> dict:
    logger.info("builder.collect_requirements")
    return {
        "current_stage": "collect_requirements",
        "collected_requirements": state.get("collected_requirements") or {},
    }


async def propose_structure(state: AgentBuilderState) -> dict:
    logger.info("builder.propose_structure")
    requirements = state.get("collected_requirements") or {}
    goal = state.get("goal", "")
    plan_steps = state.get("plan_steps") or []
    requirements_validation = validate_required_elements(requirements)
    if not requirements_validation["valid"]:
        missing = ", ".join(requirements_validation.get("missing") or [])
        return _llm_failure(f"Нельзя формировать blueprint: не заполнены элементы — {missing}")

    try:
        llm_blueprint = await builder_llm.generate_blueprint(
            goal=goal,
            requirements=requirements,
            plan_steps=plan_steps,
        )
        blueprint = blueprint_from_llm(goal, llm_blueprint, requirements)
    except BuilderLLMError as exc:
        return _llm_failure(str(exc))

    service = state.get("service")
    if service is not None:
        await service.save_blueprint_draft(
            state["session_id"],
            blueprint,
            current_user=state.get("current_user"),
        )
    blueprint_message = f"Сформирован blueprint агента «{blueprint['agent_card']['name']}»."
    requirements = finalize_requirements(dict(state.get("collected_requirements") or {}))
    conversation = _conversation(state)
    conversation = append_conversation(conversation, "assistant", blueprint_message)
    requirements = _store_conversation(requirements, conversation)
    return {
        "current_stage": "propose_structure",
        "blueprint": blueprint,
        "workflow_graph": blueprint.get("workflow_graph"),
        "collected_requirements": requirements,
        "conversation": conversation,
        "clarifying_questions": [],
        "assistant_messages": [blueprint_message],
        "status": AgentBuilderSessionStatus.GENERATED.value,
    }


async def validate_blueprint_node(state: AgentBuilderState) -> dict:
    logger.info("builder.validate_blueprint")
    blueprint = state.get("blueprint")
    validation = validate_agent_blueprint(blueprint)
    status = (
        AgentBuilderSessionStatus.NEEDS_USER_REVIEW.value
        if validation["valid"]
        else AgentBuilderSessionStatus.NEEDS_CLARIFICATION.value
    )
    service = state.get("service")
    if service is not None:
        await service.save_validation_result(state["session_id"], validation, current_user=state.get("current_user"))
    review_message = (
        "Blueprint сформирован. Результат доступен в панели справа — проверьте и нажмите «Зафиксировать структуру»."
        if validation["valid"]
        else f"Blueprint неполный: {', '.join(validation['errors'])}"
    )
    requirements = finalize_requirements(dict(state.get("collected_requirements") or {}))
    conversation = _conversation(state)
    conversation = append_conversation(conversation, "assistant", review_message)
    requirements = _store_conversation(requirements, conversation)
    return {
        "current_stage": "validate_blueprint",
        "validation_result": validation,
        "collected_requirements": requirements,
        "conversation": conversation,
        "clarifying_questions": [],
        "status": status,
        "assistant_messages": [review_message],
    }


async def prepare_preview(state: AgentBuilderState) -> dict:
    logger.info("builder.prepare_preview")
    requirements = dict(state.get("collected_requirements") or {})
    goal = state.get("goal", "")
    blueprint = state.get("blueprint")

    preview_result = await run_agent_preview(
        goal=goal,
        requirements=requirements,
        blueprint=blueprint,
    )
    requirements["preview_result"] = preview_result

    if preview_result.get("success"):
        preview_text = preview_result.get("output_text") or ""
        source = preview_result.get("source") or "preview"
        preview_message = (
            f"Пробный запуск агента выполнен ({source}).\n\n"
            f"Результат:\n{preview_text}\n\n"
            "Если результат вас устраивает — нажмите «Зафиксировать структуру»."
        )
    else:
        preview_message = (
            f"Пробный запуск не удался: {preview_result.get('error', 'неизвестная ошибка')}. "
            "Исправьте требования или пересоберите blueprint."
        )

    conversation = _conversation(state)
    conversation = append_conversation(conversation, "assistant", preview_message)
    requirements = _store_conversation(requirements, conversation)

    return {
        "current_stage": "prepare_preview",
        "collected_requirements": requirements,
        "conversation": conversation,
        "preview_result": preview_result,
        "clarifying_questions": [],
        "requires_user_input": True,
        "assistant_messages": [preview_message],
        "status": AgentBuilderSessionStatus.NEEDS_USER_REVIEW.value,
    }


async def wait_user_review(state: AgentBuilderState) -> dict:
    logger.info("builder.wait_user_review")
    return {
        "current_stage": "wait_user_review",
        "requires_user_input": True,
        "status": AgentBuilderSessionStatus.NEEDS_USER_REVIEW.value,
    }


async def finalize_blueprint(state: AgentBuilderState) -> dict:
    logger.info("builder.finalize_blueprint")
    return {
        "current_stage": "finalize_blueprint",
        "status": AgentBuilderSessionStatus.APPROVED.value,
    }


def route_after_understand(state: AgentBuilderState) -> str:
    if state.get("status") == AgentBuilderSessionStatus.FAILED.value:
        return END
    return "ask_clarifying_questions"


def route_after_clarify(state: AgentBuilderState) -> str:
    if state.get("status") == AgentBuilderSessionStatus.FAILED.value:
        return END
    if state.get("requires_user_input"):
        return END
    return "create_plan"


def route_after_create_plan(state: AgentBuilderState) -> str:
    if state.get("status") == AgentBuilderSessionStatus.FAILED.value:
        return END
    if state.get("requires_user_input"):
        return END
    return "execute_plan_step"


def route_after_execute(state: AgentBuilderState) -> str:
    index = state.get("current_step_index", 0)
    steps = state.get("plan_steps") or default_plan_steps()
    if index < len(steps):
        return "execute_plan_step"
    return "collect_requirements"


def route_after_propose(state: AgentBuilderState) -> str:
    if state.get("status") == AgentBuilderSessionStatus.FAILED.value:
        return END
    return "validate_blueprint"


def build_graph():
    graph = StateGraph(AgentBuilderState)
    nodes = [
        ("understand_goal", understand_goal),
        ("ask_clarifying_questions", ask_clarifying_questions),
        ("create_plan", create_plan),
        ("execute_plan_step", execute_plan_step),
        ("collect_requirements", collect_requirements),
        ("propose_structure", propose_structure),
        ("validate_blueprint", validate_blueprint_node),
        ("prepare_preview", prepare_preview),
        ("wait_user_review", wait_user_review),
        ("finalize_blueprint", finalize_blueprint),
    ]
    for name, fn in nodes:
        graph.add_node(name, fn)

    graph.add_edge(START, "understand_goal")
    graph.add_conditional_edges(
        "understand_goal",
        route_after_understand,
        {"ask_clarifying_questions": "ask_clarifying_questions", END: END},
    )
    graph.add_conditional_edges(
        "ask_clarifying_questions",
        route_after_clarify,
        {"create_plan": "create_plan", END: END},
    )
    graph.add_conditional_edges(
        "create_plan",
        route_after_create_plan,
        {"execute_plan_step": "execute_plan_step", END: END},
    )
    graph.add_conditional_edges(
        "execute_plan_step",
        route_after_execute,
        {"execute_plan_step": "execute_plan_step", "collect_requirements": "collect_requirements"},
    )
    graph.add_edge("collect_requirements", "propose_structure")
    graph.add_conditional_edges(
        "propose_structure",
        route_after_propose,
        {"validate_blueprint": "validate_blueprint", END: END},
    )
    graph.add_edge("validate_blueprint", "prepare_preview")
    graph.add_edge("prepare_preview", "wait_user_review")
    graph.add_edge("wait_user_review", END)
    return graph.compile()


def _llm_failure(message: str) -> dict[str, Any]:
    return {
        "status": AgentBuilderSessionStatus.FAILED.value,
        "assistant_messages": [message],
        "requires_user_input": False,
    }
