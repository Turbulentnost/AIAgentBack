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
from app.schemas.nd_process_graph import ProcessGraphDTO
from app.services.process_graph_builder import (
    ProcessGraphBuilder,
    ProcessGraphBuilderError,
    process_graph_to_uml_context,
)

logger = get_logger(__name__)

LLMChatFn = Callable[..., Awaitable[dict[str, Any]]]

UML_TYPE = "mermaid_activity"
_MERMAID_START_RE = re.compile(r"^\s*(flowchart|graph|sequenceDiagram)\b", re.IGNORECASE)
_MERMAID_FENCE_RE = re.compile(r"```(?:mermaid)?\s*([\s\S]*?)```", re.IGNORECASE)


class NdProcessUmlServiceError(Exception):
    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


def compute_content_version(graph: ProcessGraphDTO) -> str:
    payload = graph.model_dump(mode="json")
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
    from app.services.mermaid_sanitize import sanitize_mermaid_code

    return sanitize_mermaid_code(text)


def _graph_is_empty(graph: ProcessGraphDTO) -> bool:
    return not any(
        [
            graph.steps,
            graph.actors,
            graph.inputs,
            graph.outputs,
            graph.systems,
            graph.forms,
            graph.subprocesses,
        ]
    )


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

    async def get_process_uml(self, process_id: uuid.UUID, *, force: bool = False) -> dict[str, Any]:
        logger.info("nd_control.uml.start", process_id=str(process_id), force=force)
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

        content_version = compute_content_version(graph)
        logger.info(
            "nd_control.uml.context_ready",
            process_id=str(process_id),
            steps_count=len(graph.steps),
            subprocesses_count=len(graph.subprocesses),
            content_version=content_version,
        )

        cached = None if force else await self._get_cache(process_id, content_version)
        if cached:
            logger.info("nd_control.uml.cache_hit", process_id=str(process_id), content_version=content_version)
            from app.services.mermaid_sanitize import sanitize_mermaid_code

            return {
                "process_id": process_id,
                "process_name": graph.process_name,
                "uml_type": cached.uml_type,
                "uml_code": sanitize_mermaid_code(cached.uml_code),
                "cached": True,
            }

        context = process_graph_to_uml_context(graph)
        uml_code = await self._generate_mermaid(context)
        await self._save_cache(process_id, content_version, uml_code)
        await self.db.flush()
        logger.info("nd_control.uml.completed", process_id=str(process_id), content_version=content_version)
        return {
            "process_id": process_id,
            "process_name": graph.process_name,
            "uml_type": UML_TYPE,
            "uml_code": uml_code,
            "cached": False,
        }

    async def _get_cache(self, process_id: uuid.UUID, content_version: str) -> ProcessUmlCache | None:
        result = await self.db.execute(
            select(ProcessUmlCache).where(
                ProcessUmlCache.process_id == process_id,
                ProcessUmlCache.content_version == content_version,
            )
        )
        return result.scalar_one_or_none()

    async def _save_cache(self, process_id: uuid.UUID, content_version: str, uml_code: str) -> None:
        self.db.add(
            ProcessUmlCache(
                process_id=process_id,
                content_version=content_version,
                uml_type=UML_TYPE,
                uml_code=uml_code,
            )
        )

    async def _generate_mermaid(self, context: dict[str, Any]) -> str:
        user_prompt = build_process_uml_user_prompt(context)
        logger.info("nd_control.uml.llm_request", process_id=context["process"]["id"])
        try:
            response = await self._llm_chat(
                [
                    {"role": "system", "content": ND_PROCESS_UML_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
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
        logger.info("nd_control.uml.llm_response", process_id=context["process"]["id"], length=len(content))
        return extract_mermaid_code(content)
