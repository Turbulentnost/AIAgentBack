from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.builder.agent_builder import run_builder_session
from app.agents.builder.llm import append_conversation
from app.agents.builder.preview_runner import run_agent_preview
from app.agents.builder.stages import build_design_stages
from app.agents.builder.validators import validate_required_elements
from app.agents.builder.tools import slugify_code
from app.agents.builder.validators import validate_agent_blueprint
from app.agents.tools.registry import agent_tool_registry
from app.models.agent_blueprint import AgentBlueprint
from app.models.agent_builder_attempt import AgentBuilderAttempt
from app.models.agent_builder_plan import AgentBuilderPlan, AgentBuilderPlanStep
from app.models.agent_builder_sandbox import AgentBuilderSandboxRun
from app.models.agent_builder_session import AgentBuilderSession
from app.models.enums import (
    AgentBlueprintStatus,
    AgentBuilderPlanStatus,
    AgentBuilderPlanStepStatus,
    AgentBuilderSessionStatus,
)
from app.models.user import User
from app.schemas.agent_builder import (
    AgentBlueprintRead,
    AgentBuilderAttemptRead,
    AgentBuilderPlanRead,
    AgentBuilderPlanStepRead,
    AgentBuilderSessionDetailRead,
    AgentBuilderSessionRead,
    AgentBuilderToolCatalogItem,
)


class AgentBuilderServiceError(Exception):
    pass


class AgentBuilderService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_session(self, goal: str, *, current_user: User) -> AgentBuilderSession:
        session = AgentBuilderSession(
            user_id=current_user.id,
            goal=goal.strip(),
            status=AgentBuilderSessionStatus.DRAFT,
            collected_requirements={"goal": goal.strip()},
        )
        self.db.add(session)
        await self.db.flush()
        await self._record_attempt(session, goal=goal, success=True, result_summary="Сессия создана")
        return session

    async def list_sessions(self, *, current_user: User, limit: int = 50) -> list[AgentBuilderSession]:
        stmt = select(AgentBuilderSession).where(AgentBuilderSession.user_id == current_user.id)
        if current_user.is_superuser:
            stmt = select(AgentBuilderSession)
        stmt = stmt.order_by(AgentBuilderSession.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_session(self, session_id: uuid.UUID, *, current_user: User) -> AgentBuilderSession:
        session = await self._load_session(session_id)
        self._ensure_access(session, current_user)
        return session

    async def delete_session(self, session_id: uuid.UUID, *, current_user: User) -> None:
        session = await self.get_session(session_id, current_user=current_user)
        blueprints = await self._list_blueprints_for_session(session.id)
        for blueprint in blueprints:
            await self.db.delete(blueprint)
        await self.db.delete(session)
        await self.db.flush()

    async def get_session_detail(
        self,
        session_id: uuid.UUID,
        *,
        current_user: User,
        assistant_messages: list[str] | None = None,
        clarifying_questions: list[str] | None = None,
    ) -> AgentBuilderSessionDetailRead:
        session = await self.get_session(session_id, current_user=current_user)
        plan = await self._latest_plan(session.id)
        attempts = await self._list_attempts(session.id)
        blueprint = await self._latest_blueprint(session.id)
        plan_read = None
        if plan is not None:
            plan.steps.sort(key=lambda item: item.step_order)
            plan_read = AgentBuilderPlanRead.model_validate(plan)

        reqs = session.collected_requirements or {}
        conversation = reqs.get("conversation") or []
        if not assistant_messages and isinstance(conversation, list):
            assistant_messages = [
                str(item.get("content"))
                for item in conversation
                if isinstance(item, dict) and item.get("role") == "assistant" and item.get("content")
            ]
        if clarifying_questions is None:
            clarifying_questions = reqs.get("pending_questions") or []

        from app.schemas.agent_builder import (
            AgentBuilderDesignStageRead,
            AgentBuilderPreviewRead,
            AgentBuilderRequiredElementRead,
        )

        required_elements_raw = reqs.get("required_elements") or []
        preview_raw = reqs.get("preview_result")
        requirements_validation = reqs.get("requirements_validation") or validate_required_elements(reqs)

        if session.status != AgentBuilderSessionStatus.NEEDS_CLARIFICATION:
            clarifying_questions = []
        elif requirements_validation.get("valid"):
            clarifying_questions = []
        design_stages = build_design_stages(session.current_stage, session.status.value)
        proposal_meta = reqs.get("agent_type_proposal_meta") or {}
        from app.schemas.agent_builder import AgentTypeProposalRead

        agent_type_proposal = AgentTypeProposalRead(
            proposed_agent_type=reqs.get("agent_type_proposal"),
            confidence=proposal_meta.get("confidence"),
            reasoning=proposal_meta.get("reasoning"),
            confirmed=bool(reqs.get("agent_type_confirmed")),
        )

        return AgentBuilderSessionDetailRead(
            id=session.id,
            goal=session.goal,
            current_stage=session.current_stage,
            status=session.status,
            collected_requirements=session.collected_requirements,
            validation_result=session.validation_result,
            proposed_agent_structure=session.proposed_agent_structure,
            created_at=session.created_at,
            updated_at=session.updated_at,
            plan=plan_read,
            attempts=[AgentBuilderAttemptRead.model_validate(item) for item in attempts],
            blueprint=AgentBlueprintRead.model_validate(blueprint) if blueprint else None,
            assistant_messages=assistant_messages or [],
            clarifying_questions=clarifying_questions or [],
            design_stages=[AgentBuilderDesignStageRead.model_validate(item) for item in design_stages],
            required_elements=[
                AgentBuilderRequiredElementRead.model_validate(item)
                for item in required_elements_raw
                if isinstance(item, dict)
            ],
            requirements_validation=requirements_validation,
            preview_result=AgentBuilderPreviewRead.model_validate(preview_raw)
            if isinstance(preview_raw, dict)
            else None,
            agent_type=reqs.get("agent_type") or (blueprint.agent_type if blueprint else None),
            agent_type_proposal=agent_type_proposal,
        )

    async def send_message(self, session_id: uuid.UUID, message: str, *, current_user: User) -> AgentBuilderSessionDetailRead:
        session = await self.get_session(session_id, current_user=current_user)
        graph_result = await run_builder_session(
            session_id=str(session.id),
            goal=session.goal,
            service=self,
            current_user=current_user,
            user_message=message,
            collected_requirements=session.collected_requirements or {},
        )
        self._apply_graph_result(session, graph_result)
        await self.db.flush()
        if graph_result.get("status") == AgentBuilderSessionStatus.FAILED.value:
            error_message = (graph_result.get("assistant_messages") or ["Ошибка конструктора"])[0]
            raise AgentBuilderServiceError(error_message)
        await self._record_attempt(
            session,
            goal=message,
            success=True,
            result_summary=(graph_result.get("assistant_messages") or ["Обработано"])[-1],
            input_context={"message": message},
        )
        clarifying = (
            graph_result.get("clarifying_questions")
            if session.status == AgentBuilderSessionStatus.NEEDS_CLARIFICATION
            else []
        )
        return await self.get_session_detail(
            session.id,
            current_user=current_user,
            clarifying_questions=clarifying,
        )

    async def start_design(self, session_id: uuid.UUID, *, current_user: User) -> AgentBuilderSessionDetailRead:
        session = await self.get_session(session_id, current_user=current_user)
        graph_result = await run_builder_session(
            session_id=str(session.id),
            goal=session.goal,
            service=self,
            current_user=current_user,
            collected_requirements=session.collected_requirements or {},
        )
        self._apply_graph_result(session, graph_result)
        await self.db.flush()
        if graph_result.get("status") == AgentBuilderSessionStatus.FAILED.value:
            error_message = (graph_result.get("assistant_messages") or ["Ошибка конструктора"])[0]
            raise AgentBuilderServiceError(error_message)
        await self._record_attempt(
            session,
            goal=session.goal,
            success=True,
            result_summary=(graph_result.get("assistant_messages") or ["Проектирование запущено"])[-1],
            input_context={"action": "start_design"},
        )
        clarifying = (
            graph_result.get("clarifying_questions")
            if session.status == AgentBuilderSessionStatus.NEEDS_CLARIFICATION
            else []
        )
        return await self.get_session_detail(
            session.id,
            current_user=current_user,
            clarifying_questions=clarifying,
        )

    async def get_plan(self, session_id: uuid.UUID, *, current_user: User) -> AgentBuilderPlanRead | None:
        session = await self.get_session(session_id, current_user=current_user)
        plan = await self._latest_plan(session.id)
        return AgentBuilderPlanRead.model_validate(plan) if plan else None

    async def get_attempts(self, session_id: uuid.UUID, *, current_user: User) -> list[AgentBuilderAttemptRead]:
        session = await self.get_session(session_id, current_user=current_user)
        attempts = await self._list_attempts(session.id)
        return [AgentBuilderAttemptRead.model_validate(item) for item in attempts]

    async def get_blueprint(self, session_id: uuid.UUID, *, current_user: User) -> AgentBlueprintRead | None:
        session = await self.get_session(session_id, current_user=current_user)
        blueprint = await self._latest_blueprint(session.id)
        return AgentBlueprintRead.model_validate(blueprint) if blueprint else None

    async def approve_blueprint(self, session_id: uuid.UUID, *, current_user: User) -> AgentBlueprintRead:
        session = await self.get_session(session_id, current_user=current_user)
        blueprint = await self._latest_blueprint(session.id)
        if blueprint is None:
            raise AgentBuilderServiceError("Blueprint не найден")
        if blueprint.status not in {
            AgentBlueprintStatus.GENERATED,
            AgentBlueprintStatus.NEEDS_USER_REVIEW,
        }:
            raise AgentBuilderServiceError("Blueprint нельзя утвердить в текущем статусе")
        reqs = session.collected_requirements or {}
        req_validation = validate_required_elements(reqs)
        if not req_validation["valid"]:
            missing = ", ".join(req_validation.get("missing") or [])
            raise AgentBuilderServiceError(f"Не все обязательные элементы заполнены: {missing}")

        preview = reqs.get("preview_result") or {}
        if not preview.get("success") or not preview.get("output_text"):
            raise AgentBuilderServiceError(
                "Сначала дождитесь успешного пробного запуска агента и проверьте результат"
            )

        validation = validate_agent_blueprint(self._blueprint_dict(blueprint))
        if not validation["valid"]:
            raise AgentBuilderServiceError(f"Blueprint неполный: {', '.join(validation['errors'])}")
        blueprint.status = AgentBlueprintStatus.APPROVED
        session.status = AgentBuilderSessionStatus.APPROVED
        session.validation_result = validation
        await self.db.flush()
        return AgentBlueprintRead.model_validate(blueprint)

    async def run_preview(self, session_id: uuid.UUID, *, current_user: User) -> AgentBuilderSessionDetailRead:
        session = await self.get_session(session_id, current_user=current_user)
        if session.proposed_agent_structure is None and session.status not in {
            AgentBuilderSessionStatus.GENERATED,
            AgentBuilderSessionStatus.NEEDS_USER_REVIEW,
        }:
            raise AgentBuilderServiceError("Сначала дождитесь формирования blueprint")

        reqs = dict(session.collected_requirements or {})
        preview = await run_agent_preview(
            goal=session.goal,
            requirements=reqs,
            blueprint=session.proposed_agent_structure,
            db=self.db,
            user=current_user,
        )
        reqs["preview_result"] = preview
        conversation = reqs.get("conversation") if isinstance(reqs.get("conversation"), list) else []
        if preview.get("success"):
            message = (
                f"Пробный запуск агента выполнен ({preview.get('source', 'preview')}).\n\n"
                f"Результат:\n{preview.get('output_text', '')}"
            )
        else:
            message = f"Пробный запуск не удался: {preview.get('error', 'неизвестная ошибка')}"
        conversation = append_conversation(conversation, "assistant", message)
        reqs["conversation"] = conversation
        session.collected_requirements = reqs
        session.current_stage = "prepare_preview"
        if preview.get("success"):
            session.status = AgentBuilderSessionStatus.NEEDS_USER_REVIEW
        await self.db.flush()
        await self._record_attempt(
            session,
            goal="Пробный запуск агента",
            success=bool(preview.get("success")),
            result_summary=(preview.get("output_text") or preview.get("error") or "Пробный запуск")[:500],
            input_context={"preview_type": preview.get("preview_type")},
        )
        return await self.get_session_detail(session.id, current_user=current_user)

    async def start_sandbox_run(
        self,
        session_id: uuid.UUID,
        *,
        test_query: str | None,
        current_user: User,
    ) -> AgentBuilderSandboxRun:
        session = await self.get_session(session_id, current_user=current_user)
        if session.proposed_agent_structure is None:
            raise AgentBuilderServiceError("Сначала дождитесь формирования blueprint")

        query = (test_query or "").strip() or session.goal
        run = AgentBuilderSandboxRun(
            session_id=session.id,
            requested_by_user_id=current_user.id,
            status="pending",
            test_query=query,
        )
        self.db.add(run)
        await self.db.flush()
        run_id = run.id
        await self.db.commit()

        from app.workers.tasks import run_sandbox

        run_sandbox.apply_async(args=[str(run_id)], queue="agents")
        loaded = await self._load_sandbox_run(run_id)
        if loaded is None:
            raise AgentBuilderServiceError("Не удалось создать пробный запуск")
        return loaded

    async def get_sandbox_run(
        self,
        session_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        current_user: User,
    ) -> AgentBuilderSandboxRun:
        session = await self.get_session(session_id, current_user=current_user)
        run = await self._load_sandbox_run(run_id)
        if run is None or run.session_id != session.id:
            raise AgentBuilderServiceError("Sandbox run не найден")
        return run

    async def get_latest_sandbox_run(
        self,
        session_id: uuid.UUID,
        *,
        current_user: User,
    ) -> AgentBuilderSandboxRun | None:
        session = await self.get_session(session_id, current_user=current_user)
        stmt = (
            select(AgentBuilderSandboxRun)
            .where(AgentBuilderSandboxRun.session_id == session.id)
            .options(selectinload(AgentBuilderSandboxRun.steps))
            .order_by(AgentBuilderSandboxRun.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _load_sandbox_run(self, run_id: uuid.UUID) -> AgentBuilderSandboxRun | None:
        stmt = (
            select(AgentBuilderSandboxRun)
            .where(AgentBuilderSandboxRun.id == run_id)
            .options(selectinload(AgentBuilderSandboxRun.steps))
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def regenerate(self, session_id: uuid.UUID, *, current_user: User) -> AgentBuilderSessionDetailRead:
        session = await self.get_session(session_id, current_user=current_user)
        session.status = AgentBuilderSessionStatus.PLANNING
        session.current_stage = None
        session.validation_result = None
        await self.db.flush()
        return await self.start_design(session.id, current_user=current_user)

    def list_tool_catalog(self) -> list[AgentBuilderToolCatalogItem]:
        return [
            AgentBuilderToolCatalogItem(
                name=tool.name,
                description=tool.description,
                implemented=tool.implemented,
                required_permissions=tool.required_permissions,
            )
            for tool in agent_tool_registry.list()
        ]

    async def save_plan(self, session_id: str, steps: list[dict[str, str]], *, current_user: User) -> AgentBuilderPlan:
        session = await self.get_session(uuid.UUID(session_id), current_user=current_user)
        plan = AgentBuilderPlan(
            session_id=session.id,
            goal=session.goal,
            status=AgentBuilderPlanStatus.ACTIVE,
            created_by_agent=True,
        )
        self.db.add(plan)
        await self.db.flush()
        for index, step in enumerate(steps):
            self.db.add(
                AgentBuilderPlanStep(
                    plan_id=plan.id,
                    step_order=index,
                    title=step["title"],
                    description=step.get("description"),
                    status=AgentBuilderPlanStepStatus.PENDING,
                )
            )
        await self.db.flush()
        return plan

    async def complete_plan_step(
        self,
        session_id: str,
        *,
        step_order: int,
        result: dict[str, Any],
        current_user: User,
    ) -> None:
        session = await self.get_session(uuid.UUID(session_id), current_user=current_user)
        plan = await self._latest_plan(session.id)
        if plan is None:
            return
        step = next((item for item in plan.steps if item.step_order == step_order), None)
        if step is None:
            return
        step.status = AgentBuilderPlanStepStatus.COMPLETED
        step.started_at = step.started_at or datetime.now(UTC)
        step.finished_at = datetime.now(UTC)
        step.result = result
        await self.db.flush()

    async def save_blueprint_draft(
        self,
        session_id: str | uuid.UUID,
        blueprint: dict[str, Any],
        *,
        current_user: User,
    ) -> AgentBlueprint:
        sid = uuid.UUID(str(session_id))
        session = await self.get_session(sid, current_user=current_user)
        agent_card = blueprint.get("agent_card") or {}
        name = agent_card.get("name") or session.goal[:80]
        code = slugify_code(name)
        agent_type = blueprint.get("agent_type") or (session.collected_requirements or {}).get("agent_type")
        existing = await self._latest_blueprint(session.id)
        if existing is not None:
            existing.name = name
            existing.description = agent_card.get("purpose")
            existing.agent_type = agent_type
            existing.input_schema = blueprint.get("input_schema")
            existing.output_schema = blueprint.get("output_schema")
            existing.tools = blueprint.get("tools")
            existing.knowledge_bases = blueprint.get("knowledge_bases")
            existing.workflow_graph = blueprint.get("workflow_graph")
            existing.human_approval_rules = blueprint.get("human_approval_rules")
            existing.prompts = blueprint.get("prompts")
            existing.test_cases = blueprint.get("test_cases")
            existing.report_template = blueprint.get("report_template")
            existing.metadata_ = {
                "agent_card": agent_card,
                "constraints": blueprint.get("constraints", []),
                "task_statuses": blueprint.get("task_statuses", []),
                "finding_schema": blueprint.get("finding_schema", {}),
            }
            existing.status = AgentBlueprintStatus.GENERATED
            await self.db.flush()
            session.proposed_agent_structure = blueprint
            return existing

        item = AgentBlueprint(
            name=name,
            code=code,
            description=agent_card.get("purpose"),
            agent_type=agent_type,
            created_by_user_id=current_user.id,
            created_by_builder_session_id=session.id,
            status=AgentBlueprintStatus.GENERATED,
            input_schema=blueprint.get("input_schema"),
            output_schema=blueprint.get("output_schema"),
            tools=blueprint.get("tools"),
            knowledge_bases=blueprint.get("knowledge_bases"),
            workflow_graph=blueprint.get("workflow_graph"),
            human_approval_rules=blueprint.get("human_approval_rules"),
            prompts=blueprint.get("prompts"),
            test_cases=blueprint.get("test_cases"),
            report_template=blueprint.get("report_template"),
            metadata_={
                "agent_card": agent_card,
                "constraints": blueprint.get("constraints", []),
                "task_statuses": blueprint.get("task_statuses", []),
                "finding_schema": blueprint.get("finding_schema", {}),
            },
        )
        self.db.add(item)
        session.proposed_agent_structure = blueprint
        session.status = AgentBuilderSessionStatus.GENERATED
        await self.db.flush()
        return item

    async def save_validation_result(
        self,
        session_id: str,
        validation: dict[str, Any],
        *,
        current_user: User,
    ) -> None:
        session = await self.get_session(uuid.UUID(session_id), current_user=current_user)
        session.validation_result = validation
        blueprint = await self._latest_blueprint(session.id)
        if blueprint is not None:
            blueprint.status = (
                AgentBlueprintStatus.NEEDS_USER_REVIEW if validation.get("valid") else AgentBlueprintStatus.DRAFT
            )
        session.status = (
            AgentBuilderSessionStatus.NEEDS_USER_REVIEW
            if validation.get("valid")
            else AgentBuilderSessionStatus.NEEDS_CLARIFICATION
        )
        await self.db.flush()

    def _apply_graph_result(self, session: AgentBuilderSession, graph_result: dict[str, Any]) -> None:
        session.current_stage = graph_result.get("current_stage")
        if graph_result.get("status"):
            session.status = AgentBuilderSessionStatus(graph_result["status"])
        if graph_result.get("collected_requirements") is not None:
            session.collected_requirements = graph_result["collected_requirements"]
        if graph_result.get("validation_result") is not None:
            session.validation_result = graph_result["validation_result"]
        if graph_result.get("blueprint") is not None:
            session.proposed_agent_structure = graph_result["blueprint"]

    async def _load_session(self, session_id: uuid.UUID) -> AgentBuilderSession:
        stmt = (
            select(AgentBuilderSession)
            .where(AgentBuilderSession.id == session_id)
            .execution_options(populate_existing=True)
        )
        result = await self.db.execute(stmt)
        session = result.scalar_one_or_none()
        if session is None:
            raise AgentBuilderServiceError("Сессия не найдена")
        await self.db.refresh(session)
        return session

    def _ensure_access(self, session: AgentBuilderSession, current_user: User) -> None:
        if current_user.is_superuser or session.user_id == current_user.id:
            return
        raise AgentBuilderServiceError("Нет доступа к сессии")

    async def _latest_plan(self, session_id: uuid.UUID) -> AgentBuilderPlan | None:
        stmt = (
            select(AgentBuilderPlan)
            .where(AgentBuilderPlan.session_id == session_id)
            .options(selectinload(AgentBuilderPlan.steps))
            .order_by(AgentBuilderPlan.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _latest_blueprint(self, session_id: uuid.UUID) -> AgentBlueprint | None:
        blueprints = await self._list_blueprints_for_session(session_id)
        return blueprints[0] if blueprints else None

    async def _list_blueprints_for_session(self, session_id: uuid.UUID) -> list[AgentBlueprint]:
        stmt = (
            select(AgentBlueprint)
            .where(AgentBlueprint.created_by_builder_session_id == session_id)
            .order_by(AgentBlueprint.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _list_attempts(self, session_id: uuid.UUID) -> list[AgentBuilderAttempt]:
        stmt = (
            select(AgentBuilderAttempt)
            .where(AgentBuilderAttempt.session_id == session_id)
            .order_by(AgentBuilderAttempt.attempt_number.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _record_attempt(
        self,
        session: AgentBuilderSession,
        *,
        goal: str | None,
        success: bool,
        result_summary: str | None = None,
        failure_reason: str | None = None,
        input_context: dict[str, Any] | None = None,
    ) -> AgentBuilderAttempt:
        attempts = await self._list_attempts(session.id)
        attempt = AgentBuilderAttempt(
            session_id=session.id,
            attempt_number=(attempts[0].attempt_number + 1) if attempts else 1,
            goal=goal,
            success=success,
            result_summary=result_summary,
            failure_reason=failure_reason,
            input_context=input_context,
        )
        self.db.add(attempt)
        await self.db.flush()
        return attempt

    def _blueprint_dict(self, blueprint: AgentBlueprint) -> dict[str, Any]:
        metadata = blueprint.metadata_ or {}
        return {
            "agent_card": metadata.get("agent_card")
            or {"name": blueprint.name, "purpose": blueprint.description or "", "roles": []},
            "input_schema": blueprint.input_schema or {},
            "output_schema": blueprint.output_schema or {},
            "tools": blueprint.tools or [],
            "knowledge_bases": blueprint.knowledge_bases or [],
            "workflow_graph": blueprint.workflow_graph or {},
            "human_approval_rules": blueprint.human_approval_rules or [],
            "prompts": blueprint.prompts or {},
            "test_cases": blueprint.test_cases or [],
            "report_template": blueprint.report_template or {},
            "constraints": metadata.get("constraints", []),
            "task_statuses": metadata.get("task_statuses", []),
            "finding_schema": metadata.get("finding_schema", {}),
        }
