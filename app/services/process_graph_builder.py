from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.enums import NdGraphEntityType, NdRelationType
from app.models.nd_control_structural import NdRelation, ProcessCard
from app.schemas.nd_process_graph import ProcessGraphDTO, ProcessGraphStepItem, ProcessSubprocessItem
from app.services.nd_process_display_mapper import normalize_action_details
from app.services.nd_relation_display_mapper import RELATION_TYPE_LABELS

logger = get_logger(__name__)


class ProcessGraphBuilderError(Exception):
    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


def _parse_process_id(process_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(process_id))
    except (TypeError, ValueError) as exc:
        raise ProcessGraphBuilderError("Некорректный process_id", code="invalid_process_id") from exc


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


def _counterparty_name(relation: NdRelation) -> str | None:
    if relation.target_type in {
        NdGraphEntityType.ROLE,
        NdGraphEntityType.FORM,
        NdGraphEntityType.SYSTEM,
        NdGraphEntityType.RESOURCE,
        NdGraphEntityType.PROCESS,
    }:
        return relation.target_name
    if relation.source_type in {
        NdGraphEntityType.ROLE,
        NdGraphEntityType.FORM,
        NdGraphEntityType.SYSTEM,
        NdGraphEntityType.RESOURCE,
        NdGraphEntityType.PROCESS,
    }:
        return relation.source_name
    return relation.target_name or relation.source_name


def _related_process_id(relation: NdRelation, process_id: uuid.UUID) -> uuid.UUID | None:
    if relation.relation_type != NdRelationType.PROCESS_RELATED_TO_PROCESS:
        return None
    if relation.source_type == NdGraphEntityType.PROCESS and relation.source_id == process_id:
        return relation.target_id if relation.target_type == NdGraphEntityType.PROCESS else None
    if relation.target_type == NdGraphEntityType.PROCESS and relation.target_id == process_id:
        return relation.source_id if relation.source_type == NdGraphEntityType.PROCESS else None
    return None


def _relation_direction(relation: NdRelation, process_id: uuid.UUID) -> str:
    if relation.source_type == NdGraphEntityType.PROCESS and relation.source_id == process_id:
        return "outgoing"
    if relation.target_type == NdGraphEntityType.PROCESS and relation.target_id == process_id:
        return "incoming"
    return "related"


def extract_graph_fragments(
    relations: list[NdRelation],
    *,
    process_id: uuid.UUID | None = None,
) -> dict[str, list[str]]:
    actors: list[str] = []
    inputs: list[str] = []
    outputs: list[str] = []
    systems: list[str] = []
    forms: list[str] = []

    for relation in relations:
        name = _counterparty_name(relation)
        if not name:
            continue

        if relation.relation_type == NdRelationType.PROCESS_HAS_ROLE:
            actors.append(name)
        elif relation.relation_type == NdRelationType.PROCESS_CONSUMES_INPUT:
            inputs.append(name)
        elif relation.relation_type == NdRelationType.PROCESS_PRODUCES_OUTPUT:
            outputs.append(name)
        elif relation.relation_type == NdRelationType.PROCESS_USES_FORM:
            forms.append(name)
        elif relation.relation_type == NdRelationType.PROCESS_USES_SYSTEM:
            systems.append(name)
        elif relation.relation_type == NdRelationType.PROCESS_RELATED_TO_PROCESS and process_id is None:
            continue

    return {
        "actors": _unique_strings(actors),
        "inputs": _unique_strings(inputs),
        "outputs": _unique_strings(outputs),
        "systems": _unique_strings(systems),
        "forms": _unique_strings(forms),
    }


def _build_steps(process: ProcessCard) -> list[ProcessGraphStepItem]:
    details = normalize_action_details(process.actions_json)
    return [
        ProcessGraphStepItem(
            name=item["name"],
            performer=item.get("performer"),
            controller=item.get("controller"),
            system_or_resource=item.get("system_or_resource"),
        )
        for item in details
    ]


def assemble_process_graph(
    process: ProcessCard,
    relations: list[NdRelation],
    *,
    neighbor_processes: dict[uuid.UUID, ProcessCard],
    neighbor_relations: dict[uuid.UUID, list[NdRelation]],
) -> ProcessGraphDTO:
    process_id = process.id
    fragments = extract_graph_fragments(relations, process_id=process_id)

    subprocesses: list[ProcessSubprocessItem] = []
    for relation in relations:
        if relation.relation_type != NdRelationType.PROCESS_RELATED_TO_PROCESS:
            continue

        neighbor_id = _related_process_id(relation, process_id)
        neighbor = neighbor_processes.get(neighbor_id) if neighbor_id else None
        neighbor_name = neighbor.canonical_name if neighbor else _counterparty_name(relation) or "Связанный процесс"
        neighbor_frags = extract_graph_fragments(neighbor_relations.get(neighbor_id, [])) if neighbor_id else {
            "actors": [],
            "inputs": [],
            "outputs": [],
            "systems": [],
            "forms": [],
        }

        subprocesses.append(
            ProcessSubprocessItem(
                process_id=str(neighbor_id) if neighbor_id else None,
                name=neighbor_name,
                relation_type=relation.relation_type.value,
                relation_type_label=RELATION_TYPE_LABELS.get(
                    relation.relation_type, relation.relation_type.value
                ),
                direction=_relation_direction(relation, process_id),
                actors=neighbor_frags["actors"],
                inputs=neighbor_frags["inputs"],
                outputs=neighbor_frags["outputs"],
                systems=neighbor_frags["systems"],
                forms=neighbor_frags["forms"],
            )
        )

    return ProcessGraphDTO(
        process_id=str(process.id),
        process_name=process.canonical_name,
        actors=fragments["actors"],
        steps=_build_steps(process),
        inputs=fragments["inputs"],
        outputs=fragments["outputs"],
        systems=fragments["systems"],
        forms=fragments["forms"],
        subprocesses=subprocesses,
    )


class ProcessGraphBuilder:
    """Сборка нормализованного графа процесса только из ProcessCard и NdRelation."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def build_process_graph(self, process_id: str) -> ProcessGraphDTO:
        parsed_id = _parse_process_id(process_id)
        logger.info("nd_control.process_graph.start", process_id=str(parsed_id))

        process = await self._load_process(parsed_id)
        relations = await self._load_outgoing_relations(parsed_id)
        logger.info(
            "nd_control.process_graph.relations_loaded",
            process_id=str(parsed_id),
            count=len(relations),
        )

        neighbor_ids = self._collect_neighbor_ids(parsed_id, relations)
        neighbor_processes = await self._load_neighbor_processes(neighbor_ids)
        neighbor_relations = await self._load_neighbor_relations(neighbor_ids)
        logger.info(
            "nd_control.process_graph.neighbors_expanded",
            process_id=str(parsed_id),
            neighbors_count=len(neighbor_processes),
        )

        graph = assemble_process_graph(
            process,
            relations,
            neighbor_processes=neighbor_processes,
            neighbor_relations=neighbor_relations,
        )
        logger.info(
            "nd_control.process_graph.completed",
            process_id=str(parsed_id),
            actors=len(graph.actors),
            steps=len(graph.steps),
            subprocesses=len(graph.subprocesses),
        )
        return graph

    async def _load_process(self, process_id: uuid.UUID) -> ProcessCard:
        process = await self.db.get(ProcessCard, process_id)
        if not process:
            raise ProcessGraphBuilderError("Процесс не найден", code="process_not_found")
        return process

    async def _load_outgoing_relations(self, process_id: uuid.UUID) -> list[NdRelation]:
        result = await self.db.execute(
            select(NdRelation).where(
                NdRelation.source_type == NdGraphEntityType.PROCESS,
                NdRelation.source_id == process_id,
            )
        )
        return list(result.scalars().all())

    def _collect_neighbor_ids(self, process_id: uuid.UUID, relations: list[NdRelation]) -> set[uuid.UUID]:
        neighbor_ids: set[uuid.UUID] = set()
        for relation in relations:
            neighbor_id = _related_process_id(relation, process_id)
            if neighbor_id:
                neighbor_ids.add(neighbor_id)
        return neighbor_ids

    async def _load_neighbor_processes(self, neighbor_ids: set[uuid.UUID]) -> dict[uuid.UUID, ProcessCard]:
        if not neighbor_ids:
            return {}
        result = await self.db.execute(select(ProcessCard).where(ProcessCard.id.in_(neighbor_ids)))
        return {item.id: item for item in result.scalars().all()}

    async def _load_neighbor_relations(self, neighbor_ids: set[uuid.UUID]) -> dict[uuid.UUID, list[NdRelation]]:
        if not neighbor_ids:
            return {}
        result = await self.db.execute(
            select(NdRelation).where(
                NdRelation.source_type == NdGraphEntityType.PROCESS,
                NdRelation.source_id.in_(neighbor_ids),
            )
        )
        grouped: dict[uuid.UUID, list[NdRelation]] = {item: [] for item in neighbor_ids}
        for relation in result.scalars().all():
            if relation.source_id in grouped:
                grouped[relation.source_id].append(relation)
        return grouped


def process_graph_to_uml_context(graph: ProcessGraphDTO) -> dict[str, Any]:
    """Адаптер ProcessGraphDTO → контекст для LLM/Mermaid."""
    relations: list[dict[str, Any]] = []
    for subprocess in graph.subprocesses:
        relations.append(
            {
                "relation_type": subprocess.relation_type,
                "relation_type_label": subprocess.relation_type_label,
                "direction": subprocess.direction,
                "entity_type": "Process",
                "entity_name": subprocess.name,
            }
        )

    return {
        "process": {
            "id": graph.process_id,
            "name": graph.process_name,
        },
        "actors": graph.actors,
        "steps": [step.model_dump() for step in graph.steps],
        "inputs": graph.inputs,
        "outputs": graph.outputs,
        "systems": graph.systems,
        "forms": graph.forms,
        "dependencies": relations,
        "related_processes": [
            {
                "process_id": item.process_id,
                "name": item.name,
                "relation_type": item.relation_type,
                "relation_type_label": item.relation_type_label,
                "direction": item.direction,
            }
            for item in graph.subprocesses
        ],
    }
