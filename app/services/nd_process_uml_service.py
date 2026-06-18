from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.nd_control_agent.prompts.nd_process_uml_prompt import (
    ND_PROCESS_UML_SYSTEM_PROMPT,
    build_process_uml_user_prompt,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.llm.gateway import llm_gateway
from app.models.nd_control_structural import ProcessUmlCache
from app.schemas.nd_process_graph import ProcessGraphDTO, build_schema_composition
from app.services.process_graph_builder import (
    ProcessGraphBuilder,
    ProcessGraphBuilderError,
    process_graph_to_uml_context,
)
from app.schemas.process_smk_sections import DiagramDetailLevel
from app.services.process_mermaid_validator import (
    ValidationResult,
    build_process_uml_retry_prompt,
    validate_process_mermaid,
)
from app.services.process_uml_detail import apply_detail_level_to_context
from app.services.mermaid_sanitize import repair_mermaid_code

logger = get_logger(__name__)

LLMChatFn = Callable[..., Awaitable[dict[str, Any]]]

UML_TYPE = "mermaid_flowchart_sto"
UML_GENERATOR_VERSION = "2.3.0-sto"
UML_STANDARD_PROFILE = "STO-34-003_GOST-19.701-90"
_MERMAID_START_RE = re.compile(r"^\s*(flowchart|graph|sequenceDiagram)\b", re.IGNORECASE)
_MERMAID_FENCE_RE = re.compile(r"```(?:mermaid)?\s*([\s\S]*?)```", re.IGNORECASE)


class NdProcessUmlServiceError(Exception):
    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


def compute_content_version(graph: ProcessGraphDTO, *, detail_level: DiagramDetailLevel) -> str:
    payload = {
        "generator_version": UML_GENERATOR_VERSION,
        "standard_profile": UML_STANDARD_PROFILE,
        "detail_level": detail_level.value,
        "graph": graph.model_dump(mode="json"),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return digest[:32]


def extract_mermaid_code(content: str) -> str:
    text = (content or "").strip()
    if not text:
        raise NdProcessUmlServiceError("LLM вернул пустой ответ", code="empty_llm_response")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        for key in ("uml_code", "mermaid", "diagram", "code"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate.strip():
                text = candidate.strip()
                break

    fence_match = _MERMAID_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    if not _MERMAID_START_RE.match(text):
        raise NdProcessUmlServiceError(
            "Ответ LLM не содержит валидный Mermaid (ожидается flowchart/graph/sequenceDiagram)",
            code="invalid_mermaid",
        )

    return repair_mermaid_code(text)


def _graph_is_empty(graph: ProcessGraphDTO) -> bool:
    return not any(
        [
            graph.actions,
            graph.roles,
            graph.inputs,
            graph.outputs,
            graph.systems,
            graph.forms,
            graph.subprocesses,
            graph.documents,
        ]
    )


def _parse_detail_level(value: str | None) -> DiagramDetailLevel:
    if not value:
        return DiagramDetailLevel.STANDARD
    try:
        return DiagramDetailLevel(value.strip().lower())
    except ValueError:
        return DiagramDetailLevel.STANDARD


def _response_payload(
    *,
    process_id: uuid.UUID,
    graph: ProcessGraphDTO,
    uml_code: str,
    uml_type: str,
    cached: bool,
    validation: ValidationResult,
    detail_level: DiagramDetailLevel,
) -> dict[str, Any]:
    return {
        "process_id": process_id,
        "process_name": graph.process_name,
        "uml_type": uml_type,
        "uml_code": repair_mermaid_code(uml_code),
        "cached": cached,
        "standard_profile": UML_STANDARD_PROFILE,
        "generator_version": UML_GENERATOR_VERSION,
        "detail_level": detail_level.value,
        "source_document_type": graph.source_document_type,
        "source_document_type_label": graph.source_document_type_label,
        "qms_level": graph.qms_level,
        "qms_level_label": graph.qms_level_label,
        "diagram_profile_label": graph.diagram_profile_label,
        "primary_document_type": graph.primary_document_type,
        "validation_status": validation.status,
        "validation_errors": validation.errors,
        "warnings": _unique_strings(graph.warnings + validation.warnings),
        "schema_composition": build_schema_composition(graph).model_dump(mode="json"),
    }


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


class NdProcessUmlService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        graph_builder: ProcessGraphBuilder | None = None,
        llm_chat: LLMChatFn | None = None,
        llm_model: str | None = None,
    ) -> None:
        self.db = db
        self._graph_builder = graph_builder or ProcessGraphBuilder(db)
        self._llm_chat = llm_chat or llm_gateway.chat
        self._llm_model = llm_model or settings.ND_CONTROL_UML_MODEL or settings.ND_CONTROL_EXTRACTION_MODEL

    async def get_process_uml(
        self,
        process_id: uuid.UUID,
        *,
        force: bool = False,
        detail_level: str | DiagramDetailLevel = DiagramDetailLevel.STANDARD,
    ) -> dict[str, Any]:
        level = detail_level if isinstance(detail_level, DiagramDetailLevel) else _parse_detail_level(detail_level)
        logger.info(
            "nd_control.uml.start",
            process_id=str(process_id),
            force=force,
            detail_level=level.value,
        )
        try:
            graph = await self._graph_builder.build_process_graph(str(process_id))
        except ProcessGraphBuilderError as exc:
            if exc.code == "process_not_found":
                raise NdProcessUmlServiceError(str(exc), code=exc.code) from exc
            raise NdProcessUmlServiceError(str(exc), code=exc.code) from exc

        if _graph_is_empty(graph):
            raise NdProcessUmlServiceError(
                "Недостаточно связей или данных для построения диаграммы",
                code="insufficient_data",
            )

        content_version = compute_content_version(graph, detail_level=level)
        logger.info(
            "nd_control.uml.context_ready",
            process_id=str(process_id),
            actions_count=len(graph.actions),
            subprocesses_count=len(graph.subprocesses),
            content_version=content_version,
            generator_version=UML_GENERATOR_VERSION,
            detail_level=level.value,
        )

        cached = None if force else await self._get_cache(process_id, content_version, level)
        if cached:
            logger.info("nd_control.uml.cache_hit", process_id=str(process_id), content_version=content_version)
            validation = ValidationResult(
                is_valid=cached.validation_status != "invalid",
                errors=list(cached.validation_errors or []),
                warnings=[],
            )
            return _response_payload(
                process_id=process_id,
                graph=graph,
                uml_code=repair_mermaid_code(cached.uml_code),
                uml_type=cached.uml_type,
                cached=True,
                validation=validation,
                detail_level=level,
            )

        context = apply_detail_level_to_context(process_graph_to_uml_context(graph), level)
        uml_code, validation = await self._generate_mermaid(context, graph, level)
        uml_code = repair_mermaid_code(uml_code)
        await self._save_cache(process_id, content_version, uml_code, graph, validation, level)
        await self.db.flush()
        logger.info(
            "nd_control.uml.completed",
            process_id=str(process_id),
            content_version=content_version,
            validation_status=validation.status,
        )
        return _response_payload(
            process_id=process_id,
            graph=graph,
            uml_code=uml_code,
            uml_type=UML_TYPE,
            cached=False,
            validation=validation,
            detail_level=level,
        )

    async def _get_cache(
        self,
        process_id: uuid.UUID,
        content_version: str,
        detail_level: DiagramDetailLevel,
    ) -> ProcessUmlCache | None:
        result = await self.db.execute(
            select(ProcessUmlCache).where(
                ProcessUmlCache.process_id == process_id,
                ProcessUmlCache.content_version == content_version,
                ProcessUmlCache.generator_version == UML_GENERATOR_VERSION,
                ProcessUmlCache.detail_level == detail_level.value,
            )
        )
        return result.scalar_one_or_none()

    async def _save_cache(
        self,
        process_id: uuid.UUID,
        content_version: str,
        uml_code: str,
        graph: ProcessGraphDTO,
        validation: ValidationResult,
        detail_level: DiagramDetailLevel,
    ) -> None:
        self.db.add(
            ProcessUmlCache(
                process_id=process_id,
                content_version=content_version,
                uml_type=UML_TYPE,
                uml_code=uml_code,
                generator_version=UML_GENERATOR_VERSION,
                standard_profile=UML_STANDARD_PROFILE,
                input_hash=content_version,
                validation_status=validation.status,
                validation_errors=validation.errors,
                detail_level=detail_level.value,
            )
        )

    async def _generate_mermaid(
        self,
        context: dict[str, Any],
        graph: ProcessGraphDTO,
        detail_level: DiagramDetailLevel,
    ) -> tuple[str, ValidationResult]:
        user_prompt = build_process_uml_user_prompt(context, detail_level=detail_level)
        messages = [
            {"role": "system", "content": ND_PROCESS_UML_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        uml_code = await self._call_llm(messages, process_id=context["process"]["id"])
        validation = validate_process_mermaid(uml_code, graph)

        if not validation.is_valid:
            logger.warning(
                "nd_control.uml.validation_failed",
                process_id=context["process"]["id"],
                errors=validation.errors,
            )
            retry_prompt = build_process_uml_retry_prompt(
                validation.errors,
                orphan_nodes=validation.orphan_nodes,
            )
            messages.extend(
                [
                    {"role": "assistant", "content": uml_code},
                    {"role": "user", "content": retry_prompt},
                ]
            )
            uml_code = await self._call_llm(messages, process_id=context["process"]["id"])
            validation = validate_process_mermaid(uml_code, graph)

        if not validation.is_valid:
            raise NdProcessUmlServiceError(
                "Не удалось построить блок-схему по СТО-34-003: "
                + "; ".join(validation.errors[:3]),
                code="invalid_mermaid",
            )

        return uml_code, validation

    async def _call_llm(self, messages: list[dict[str, str]], *, process_id: str) -> str:
        logger.info("nd_control.uml.llm_request", process_id=process_id)
        try:
            response = await self._llm_chat(
                messages,
                model=self._llm_model,
                temperature=0.1,
                max_tokens=4000,
                timeout=settings.ND_CONTROL_UML_LLM_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            detail = str(exc).strip() or repr(exc)
            logger.warning("nd_control.uml.llm_failed", error=detail)
            raise NdProcessUmlServiceError(
                f"Ошибка вызова LLM ({type(exc).__name__}): {detail}",
                code="llm_error",
            ) from exc

        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or message.get("reasoning") or message.get("reasoning_content") or ""
        logger.info("nd_control.uml.llm_response", process_id=process_id, length=len(content))
        return extract_mermaid_code(content)
