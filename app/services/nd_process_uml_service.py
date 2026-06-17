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
from app.models.enums import NdGraphEntityType, NdRelationType
from app.models.nd_control_structural import NdRelation, ProcessCard, ProcessUmlCache
from app.services.nd_control_department_detail_service import _normalize_string_list
from app.services.nd_process_display_mapper import normalize_action_details
from app.services.nd_relation_display_mapper import RELATION_TYPE_LABELS

logger = get_logger(__name__)

LLMChatFn = Callable[..., Awaitable[dict[str, Any]]]

UML_TYPE = "mermaid_activity"
_MERMAID_START_RE = re.compile(r"^\s*(flowchart|graph|sequenceDiagram)\b", re.IGNORECASE)
_MERMAID_FENCE_RE = re.compile(r"```(?:mermaid)?\s*([\s\S]*?)```", re.IGNORECASE)


class NdProcessUmlServiceError(Exception):
    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


def compute_content_version(
    process: ProcessCard,
    relations: list[NdRelation],
    neighbor_processes: list[ProcessCard],
) -> str:
    payload = {
        "process": {
            "id": str(process.id),
            "updated_at": process.updated_at.isoformat() if process.updated_at else None,
            "canonical_name": process.canonical_name,
            "description": process.description,
            "goal": process.goal,
            "owner_candidate": process.owner_candidate,
            "inputs_json": process.inputs_json,
            "outputs_json": process.outputs_json,
            "actions_json": process.actions_json,
            "roles_json": process.roles_json,
            "forms_json": process.forms_json,
            "systems_json": process.systems_json,
            "resources_json": process.resources_json,
        },
        "relations": [
            {
                "id": str(relation.id),
                "updated_at": relation.updated_at.isoformat() if relation.updated_at else None,
                "relation_type": relation.relation_type.value,
                "source_type": relation.source_type.value,
                "source_id": str(relation.source_id) if relation.source_id else None,
                "target_type": relation.target_type.value,
                "target_id": str(relation.target_id) if relation.target_id else None,
            }
            for relation in sorted(relations, key=lambda item: str(item.id))
        ],
        "neighbors": [
            {
                "id": str(item.id),
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                "canonical_name": item.canonical_name,
            }
            for item in sorted(neighbor_processes, key=lambda item: str(item.id))
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return digest[:32]


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


def _relation_direction(relation: NdRelation, process_id: uuid.UUID) -> str:
    if relation.source_type == NdGraphEntityType.PROCESS and relation.source_id == process_id:
        return "outgoing"
    if relation.target_type == NdGraphEntityType.PROCESS and relation.target_id == process_id:
        return "incoming"
    return "related"


def _relation_counterparty(relation: NdRelation, process_id: uuid.UUID) -> tuple[str, str | None]:
    if relation.source_type == NdGraphEntityType.PROCESS and relation.source_id == process_id:
        return relation.target_type.value, relation.target_name
    if relation.target_type == NdGraphEntityType.PROCESS and relation.target_id == process_id:
        return relation.source_type.value, relation.source_name
    return relation.target_type.value, relation.target_name


def build_process_uml_context(
    process: ProcessCard,
    relations: list[NdRelation],
    neighbor_processes: dict[uuid.UUID, ProcessCard],
) -> dict[str, Any]:
    process_id = process.id
    actors = _unique_strings(_normalize_string_list(process.roles_json))
    inputs = _unique_strings(_normalize_string_list(process.inputs_json))
    outputs = _unique_strings(_normalize_string_list(process.outputs_json))
    systems = _unique_strings(_normalize_string_list(process.systems_json))
    forms = _unique_strings(_normalize_string_list(process.forms_json))
    steps = normalize_action_details(process.actions_json)

    dependencies: list[dict[str, Any]] = []
    related_processes: list[dict[str, Any]] = []

    for relation in relations:
        entity_type, counterparty_name = _relation_counterparty(relation, process_id)
        direction = _relation_direction(relation, process_id)
        relation_label = RELATION_TYPE_LABELS.get(relation.relation_type, relation.relation_type.value)
        dependency = {
            "relation_type": relation.relation_type.value,
            "relation_type_label": relation_label,
            "direction": direction,
            "entity_type": entity_type,
            "entity_name": counterparty_name,
            "confidence": relation.confidence.value,
            "is_confirmed": relation.is_confirmed,
        }
        dependencies.append(dependency)

        if relation.relation_type == NdRelationType.PROCESS_HAS_ROLE:
            role_name = (
                relation.target_name
                if relation.target_type == NdGraphEntityType.ROLE
                else relation.source_name
            )
            if role_name:
                actors.append(role_name)
        elif relation.relation_type == NdRelationType.ROLE_RESPONSIBLE_FOR_ACTION:
            role_name = (
                relation.source_name
                if relation.source_type == NdGraphEntityType.ROLE
                else relation.target_name
            )
            if role_name:
                actors.append(role_name)
        elif relation.relation_type == NdRelationType.PROCESS_CONSUMES_INPUT:
            if counterparty_name:
                inputs.append(counterparty_name)
        elif relation.relation_type == NdRelationType.PROCESS_PRODUCES_OUTPUT:
            if counterparty_name:
                outputs.append(counterparty_name)
        elif relation.relation_type == NdRelationType.PROCESS_USES_SYSTEM:
            if counterparty_name:
                systems.append(counterparty_name)
        elif relation.relation_type == NdRelationType.PROCESS_USES_FORM:
            if counterparty_name:
                forms.append(counterparty_name)
        elif relation.relation_type == NdRelationType.PROCESS_RELATED_TO_PROCESS:
            neighbor_id = None
            if relation.source_type == NdGraphEntityType.PROCESS and relation.source_id != process_id:
                neighbor_id = relation.source_id
            elif relation.target_type == NdGraphEntityType.PROCESS and relation.target_id != process_id:
                neighbor_id = relation.target_id
            neighbor = neighbor_processes.get(neighbor_id) if neighbor_id else None
            related_processes.append(
                {
                    "process_id": str(neighbor_id) if neighbor_id else None,
                    "name": neighbor.canonical_name if neighbor else counterparty_name,
                    "relation_type": relation.relation_type.value,
                    "relation_type_label": relation_label,
                    "direction": direction,
                }
            )

    actors = _unique_strings(actors)
    inputs = _unique_strings(inputs)
    outputs = _unique_strings(outputs)
    systems = _unique_strings(systems)
    forms = _unique_strings(forms)

    return {
        "process": {
            "id": str(process.id),
            "name": process.canonical_name,
            "description": process.description,
            "goal": process.goal,
            "owner": process.owner_candidate,
        },
        "actors": actors,
        "steps": steps,
        "inputs": inputs,
        "outputs": outputs,
        "systems": systems,
        "forms": forms,
        "dependencies": dependencies,
        "related_processes": related_processes,
    }


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
    return text


class NdProcessUmlService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        llm_chat: LLMChatFn | None = None,
        llm_model: str | None = None,
    ) -> None:
        self.db = db
        self._llm_chat = llm_chat or llm_gateway.chat
        self._llm_model = llm_model or settings.ND_CONTROL_UML_MODEL or settings.ND_CONTROL_EXTRACTION_MODEL

    async def get_process_uml(self, process_id: uuid.UUID) -> dict[str, Any]:
        logger.info("nd_control.uml.start", process_id=str(process_id))
        process = await self._load_process(process_id)
        relations = await self._load_relations(process_id)
        neighbor_processes = await self._load_one_hop_processes(process_id, relations)
        content_version = compute_content_version(process, relations, list(neighbor_processes.values()))
        logger.info(
            "nd_control.uml.context_ready",
            process_id=str(process_id),
            relations_count=len(relations),
            neighbors_count=len(neighbor_processes),
            content_version=content_version,
        )

        cached = await self._get_cache(process_id, content_version)
        if cached:
            logger.info("nd_control.uml.cache_hit", process_id=str(process_id), content_version=content_version)
            return {
                "process_id": process_id,
                "uml_type": cached.uml_type,
                "uml_code": cached.uml_code,
                "cached": True,
            }

        context = build_process_uml_context(process, relations, neighbor_processes)
        uml_code = await self._generate_mermaid(context)
        await self._save_cache(process_id, content_version, uml_code)
        await self.db.flush()
        logger.info("nd_control.uml.completed", process_id=str(process_id), content_version=content_version)
        return {
            "process_id": process_id,
            "uml_type": UML_TYPE,
            "uml_code": uml_code,
            "cached": False,
        }

    async def _load_process(self, process_id: uuid.UUID) -> ProcessCard:
        process = await self.db.get(ProcessCard, process_id)
        if not process:
            raise NdProcessUmlServiceError("Процесс не найден", code="process_not_found")
        return process

    async def _load_relations(self, process_id: uuid.UUID) -> list[NdRelation]:
        from sqlalchemy import or_

        result = await self.db.execute(
            select(NdRelation).where(
                or_(
                    (NdRelation.source_type == NdGraphEntityType.PROCESS) & (NdRelation.source_id == process_id),
                    (NdRelation.target_type == NdGraphEntityType.PROCESS) & (NdRelation.target_id == process_id),
                )
            )
        )
        relations = list(result.scalars().all())
        logger.info("nd_control.uml.relations_loaded", process_id=str(process_id), count=len(relations))
        return relations

    async def _load_one_hop_processes(
        self,
        process_id: uuid.UUID,
        relations: list[NdRelation],
    ) -> dict[uuid.UUID, ProcessCard]:
        neighbor_ids: set[uuid.UUID] = set()
        for relation in relations:
            if relation.relation_type != NdRelationType.PROCESS_RELATED_TO_PROCESS:
                continue
            if (
                relation.source_type == NdGraphEntityType.PROCESS
                and relation.source_id
                and relation.source_id != process_id
            ):
                neighbor_ids.add(relation.source_id)
            if (
                relation.target_type == NdGraphEntityType.PROCESS
                and relation.target_id
                and relation.target_id != process_id
            ):
                neighbor_ids.add(relation.target_id)

        if not neighbor_ids:
            logger.info("nd_control.uml.neighbors_loaded", process_id=str(process_id), count=0)
            return {}

        result = await self.db.execute(select(ProcessCard).where(ProcessCard.id.in_(neighbor_ids)))
        neighbors = {item.id: item for item in result.scalars().all()}
        logger.info("nd_control.uml.neighbors_loaded", process_id=str(process_id), count=len(neighbors))
        return neighbors

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
