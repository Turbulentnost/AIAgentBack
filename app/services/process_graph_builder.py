from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.enums import NdGraphEntityType, NdRelationType
from app.models.nd_control_structural import DocumentCard, NdRelation, ProcessCard
from app.schemas.diagram_block import DiagramBlockType
from app.schemas.process_smk_sections import ProcessResourceItem
from app.schemas.nd_process_graph import (
    ProcessExternalReferenceItem,
    ProcessGraphActionItem,
    ProcessGraphDTO,
    ProcessGraphMetadataItem,
    ProcessSourceDocumentTypeItem,
    ProcessSubprocessItem,
)
from app.services.process_uml_document_profile import (
    document_type_context_fields,
    select_primary_document_type,
)
from app.utils.smk_document_classification import (
    DOCUMENT_TYPE_TO_LEVEL,
    get_document_level_label,
    get_document_type_label,
)
from app.services.diagram_block_classifier import classify_diagram_block
from app.services.nd_process_display_mapper import evidence_label, normalize_action_details
from app.services.nd_relation_display_mapper import RELATION_TYPE_LABELS
from app.services.process_smk_loader import load_smk_sections_from_process

logger = get_logger(__name__)

_ARCHIVE_KEYWORDS = ("архив", "хранение", "сдача в архив", "архивирован")
_CHANGE_KEYWORDS = ("изменен", "изменение", "регистрац", "версия", "редакц")
_ISSUE_KEYWORDS = ("ознакомлен", "рассылк", "доведен", "актуализац")
_CONDITION_KEYWORDS = ("если ", "при наличии", "при отсутствии", "?")
_ARCHIVE_ACTION_KEYWORDS = ("архив", "сдать в архив", "зарегистрировать", "сохранить")


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


def _json_string_list(data: list | None, *, keys: tuple[str, ...] = ("name", "title", "value", "text")) -> list[str]:
    if not data:
        return []
    result: list[str] = []
    for item in data:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
            continue
        if isinstance(item, dict):
            for key in keys:
                value = item.get(key)
                if value and str(value).strip():
                    result.append(str(value).strip())
                    break
    return _unique_strings(result)


def _evidence_items(raw: list | dict | None) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def _counterparty_name(relation: NdRelation) -> str | None:
    if relation.target_type in {
        NdGraphEntityType.ROLE,
        NdGraphEntityType.FORM,
        NdGraphEntityType.SYSTEM,
        NdGraphEntityType.RESOURCE,
        NdGraphEntityType.PROCESS,
        NdGraphEntityType.DOCUMENT,
    }:
        return relation.target_name
    if relation.source_type in {
        NdGraphEntityType.ROLE,
        NdGraphEntityType.FORM,
        NdGraphEntityType.SYSTEM,
        NdGraphEntityType.RESOURCE,
        NdGraphEntityType.PROCESS,
        NdGraphEntityType.DOCUMENT,
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


def _action_matches_role_relation(action_title: str, relation: NdRelation) -> bool:
    title = action_title.strip().lower()
    if not title:
        return False
    for item in _evidence_items(relation.evidence_json):
        quote = str(item.get("quote") or item.get("action") or "").strip().lower()
        if quote and (quote in title or title in quote):
            return True
    return False


def _role_responsible_map(relations: list[NdRelation], process_id: uuid.UUID) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for relation in relations:
        if relation.relation_type != NdRelationType.ROLE_RESPONSIBLE_FOR_ACTION:
            continue
        role_name = None
        if relation.source_type == NdGraphEntityType.ROLE:
            role_name = relation.source_name
        elif relation.target_type == NdGraphEntityType.ROLE:
            role_name = relation.target_name
        if not role_name:
            continue
        for item in _evidence_items(relation.evidence_json):
            action_hint = str(item.get("action") or item.get("quote") or "").strip()
            if action_hint:
                mapping[action_hint.lower()] = role_name
        if relation.target_type == NdGraphEntityType.PROCESS and relation.target_id == process_id:
            mapping.setdefault("__process__", role_name)
    return mapping


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
    documents: list[str] = []
    resources: list[str] = []

    for relation in relations:
        if process_id is not None:
            is_outgoing = (
                relation.source_type == NdGraphEntityType.PROCESS and relation.source_id == process_id
            )
            is_incoming = (
                relation.target_type == NdGraphEntityType.PROCESS and relation.target_id == process_id
            )
            if not is_outgoing and not is_incoming:
                continue

        if relation.relation_type == NdRelationType.PROCESS_HAS_ROLE:
            role_name = relation.target_name if relation.target_type == NdGraphEntityType.ROLE else relation.source_name
            if role_name:
                actors.append(role_name)
        elif relation.relation_type == NdRelationType.PROCESS_CONSUMES_INPUT:
            item_name = relation.target_name if relation.target_type != NdGraphEntityType.PROCESS else relation.source_name
            if item_name:
                inputs.append(item_name)
        elif relation.relation_type == NdRelationType.PROCESS_PRODUCES_OUTPUT:
            item_name = relation.target_name if relation.target_type != NdGraphEntityType.PROCESS else relation.source_name
            if item_name:
                outputs.append(item_name)
        elif relation.relation_type == NdRelationType.PROCESS_USES_FORM:
            item_name = relation.target_name if relation.target_type != NdGraphEntityType.PROCESS else relation.source_name
            if item_name:
                forms.append(item_name)
        elif relation.relation_type == NdRelationType.PROCESS_USES_SYSTEM:
            if relation.source_type == NdGraphEntityType.SYSTEM:
                systems.append(relation.source_name)
            elif relation.target_type == NdGraphEntityType.SYSTEM:
                systems.append(relation.target_name)
        elif relation.relation_type == NdRelationType.DOCUMENT_REGULATES_PROCESS and process_id:
            if relation.source_type == NdGraphEntityType.DOCUMENT:
                documents.append(relation.source_name)
            elif relation.target_type == NdGraphEntityType.DOCUMENT:
                documents.append(relation.target_name)
        elif relation.relation_type == NdRelationType.ROLE_RESPONSIBLE_FOR_ACTION:
            if relation.source_type == NdGraphEntityType.ROLE:
                actors.append(relation.source_name)
        elif relation.relation_type == NdRelationType.PROCESS_RELATED_TO_PROCESS and process_id is None:
            continue
        elif relation.target_type == NdGraphEntityType.RESOURCE:
            resources.append(relation.target_name)
        elif relation.source_type == NdGraphEntityType.RESOURCE:
            resources.append(relation.source_name)

    return {
        "actors": _unique_strings(actors),
        "inputs": _unique_strings(inputs),
        "outputs": _unique_strings(outputs),
        "systems": _unique_strings(systems),
        "forms": _unique_strings(forms),
        "documents": _unique_strings(documents),
        "resources": _unique_strings(resources),
    }


def _derive_smk_sections(actions: list[ProcessGraphActionItem]) -> dict[str, list[str]]:
    conditions: list[str] = []
    measurement_methods: list[str] = []

    for action in actions:
        text = f"{action.title} {action.description or ''}".lower()
        if action.block_type == DiagramBlockType.DECISION or any(keyword in text for keyword in _CONDITION_KEYWORDS):
            conditions.append(action.title)
        if "метод" in text and "измер" in text:
            measurement_methods.append(action.title)

    return {
        "conditions": _unique_strings(conditions),
        "measurement_methods": _unique_strings(measurement_methods),
    }


def _metadata_items(category: str, values: list[Any], *, kind: str = "metadata") -> list[ProcessGraphMetadataItem]:
    result: list[ProcessGraphMetadataItem] = []
    for item in values:
        payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else {}
        if isinstance(item, str):
            name = item.strip()
            payload = {"value": name}
        elif isinstance(payload, dict):
            name = str(
                payload.get("name")
                or payload.get("title")
                or payload.get("risk")
                or payload.get("criterion")
                or payload.get("application")
                or payload.get("value")
                or ""
            ).strip()
        else:
            name = str(item).strip()
            payload = {"value": name}

        if not name:
            continue
        result.append(
            ProcessGraphMetadataItem(
                name=name,
                category=category,
                graph_item_kind=kind,  # type: ignore[arg-type]
                payload=payload if isinstance(payload, dict) else {"value": name},
            )
        )
    return result


def _has_archive_flow_action(actions: list[ProcessGraphActionItem]) -> bool:
    for action in actions:
        text = f"{action.title} {action.description or ''}".lower()
        if any(keyword in text for keyword in _ARCHIVE_ACTION_KEYWORDS):
            return True
    return False


def _build_actions(
    process: ProcessCard,
    relations: list[NdRelation],
    *,
    process_id: uuid.UUID,
    subprocess_names: list[str],
) -> tuple[list[ProcessGraphActionItem], list[str]]:
    details = normalize_action_details(process.actions_json)
    role_map = _role_responsible_map(relations, process_id)
    warnings: list[str] = []
    actions: list[ProcessGraphActionItem] = []

    for index, item in enumerate(details):
        title = item["name"]
        responsible_role = item.get("performer")
        if not responsible_role:
            responsible_role = role_map.get(title.lower()) or role_map.get("__process__")

        for relation in relations:
            if relation.relation_type != NdRelationType.ROLE_RESPONSIBLE_FOR_ACTION:
                continue
            if relation.source_type != NdGraphEntityType.ROLE:
                continue
            if _action_matches_role_relation(title, relation):
                responsible_role = relation.source_name

        action_payload = {
            "id": f"action_{index + 1}",
            "title": title,
            "name": title,
            "description": item.get("description"),
            "performer": responsible_role,
            "used_forms": [],
            "output_objects": [],
            "used_systems": [item["system_or_resource"]] if item.get("system_or_resource") else [],
        }
        block_type, block_warnings = classify_diagram_block(
            action_payload,
            relations=relations,
            evidence=_evidence_items(item.get("evidence")),
            linked_subprocess_names=subprocess_names,
        )
        warnings.extend(block_warnings)

        related_sections: list[str] = []
        evidence_items: list[dict[str, Any]] = []
        label = item.get("evidence_label")
        if label:
            related_sections.append(label)
        for relation in relations:
            for evidence in _evidence_items(relation.evidence_json):
                quote = str(evidence.get("quote") or "")
                if title.lower() in quote.lower():
                    evidence_items.append(evidence)
                    section = evidence_label(evidence)
                    if section:
                        related_sections.append(section)

        used_forms = [
            relation.target_name
            for relation in relations
            if relation.relation_type == NdRelationType.PROCESS_USES_FORM
            and relation.target_name
            and title.lower() in (relation.source_name or "").lower()
        ]

        actions.append(
            ProcessGraphActionItem(
                id=f"action_{index + 1}",
                title=title,
                description=item.get("description"),
                responsible_role=responsible_role,
                input_objects=[],
                output_objects=[],
                used_forms=_unique_strings(used_forms),
                used_systems=_unique_strings(action_payload["used_systems"]),
                related_document_sections=_unique_strings(related_sections),
                evidence=evidence_items,
                block_type=block_type,
                graph_item_kind=(
                    "document_artifact"
                    if block_type
                    in {
                        DiagramBlockType.DOCUMENT_OUTPUT,
                        DiagramBlockType.SUBPROCESS,
                    }
                    else "flow_step"
                ),
                controller=item.get("controller"),
                system_or_resource=item.get("system_or_resource"),
            )
        )

    return actions, _unique_strings(warnings)


def assemble_process_graph(
    process: ProcessCard,
    relations: list[NdRelation],
    *,
    neighbor_processes: dict[uuid.UUID, ProcessCard],
    neighbor_relations: dict[uuid.UUID, list[NdRelation]],
    regulating_documents: list[str] | None = None,
) -> ProcessGraphDTO:
    process_id = process.id
    fragments = extract_graph_fragments(relations, process_id=process_id)

    subprocesses: list[ProcessSubprocessItem] = []
    external_references: list[ProcessExternalReferenceItem] = []
    subprocess_names: list[str] = []

    for relation in relations:
        if relation.relation_type == NdRelationType.DOCUMENT_REGULATES_PROCESS:
            doc_name = relation.source_name if relation.source_type == NdGraphEntityType.DOCUMENT else relation.target_name
            if doc_name:
                external_references.append(
                    ProcessExternalReferenceItem(
                        name=doc_name,
                        reference_type="document",
                        relation_type_label=RELATION_TYPE_LABELS.get(relation.relation_type),
                        evidence=_evidence_items(relation.evidence_json),
                    )
                )

        if relation.relation_type != NdRelationType.PROCESS_RELATED_TO_PROCESS:
            continue

        neighbor_id = _related_process_id(relation, process_id)
        neighbor = neighbor_processes.get(neighbor_id) if neighbor_id else None
        neighbor_name = neighbor.canonical_name if neighbor else _counterparty_name(relation) or "Связанный процесс"
        subprocess_names.append(neighbor_name)
        neighbor_frags = extract_graph_fragments(neighbor_relations.get(neighbor_id, [])) if neighbor_id else {
            "actors": [],
            "inputs": [],
            "outputs": [],
            "systems": [],
            "forms": [],
            "documents": [],
            "resources": [],
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

    actions, warnings = _build_actions(
        process,
        relations,
        process_id=process_id,
        subprocess_names=subprocess_names,
    )
    action_titles = {action.id: action.title for action in actions}
    smk_sections = load_smk_sections_from_process(process, action_titles=action_titles)
    derived = _derive_smk_sections(actions)

    roles = _unique_strings(fragments["actors"] + _json_string_list(process.roles_json))
    inputs = _unique_strings(fragments["inputs"] + _json_string_list(process.inputs_json))
    outputs = _unique_strings(fragments["outputs"] + _json_string_list(process.outputs_json))
    systems = _unique_strings(fragments["systems"] + _json_string_list(process.systems_json))
    forms = _unique_strings(fragments["forms"] + _json_string_list(process.forms_json))
    documents = _unique_strings(fragments["documents"] + (regulating_documents or []))

    resources = list(smk_sections["resources"])
    for name in fragments["resources"]:
        if name and not any(item.name == name for item in resources):
            resources.append(ProcessResourceItem(name=name))

    process_metadata = {
        "resources": _metadata_items("resources", resources),
        "effectiveness_criteria": _metadata_items(
            "effectiveness_criteria",
            smk_sections["effectiveness_criteria"],
        ),
        "risks": _metadata_items("risks", smk_sections["risks"]),
        "applications": _metadata_items("applications", smk_sections["applications"], kind="reference"),
        "change_registration": _metadata_items(
            "change_registration",
            smk_sections["change_registration"],
            kind="reference",
        ),
        "issue_and_acquaintance": _metadata_items(
            "issue_and_acquaintance",
            smk_sections["issue_and_acquaintance"],
            kind="reference",
        ),
        "documentation_and_archive": _metadata_items(
            "documentation_and_archive",
            smk_sections["documentation_and_archive"],
            kind="reference" if not _has_archive_flow_action(actions) else "metadata",
        ),
    }

    graph_evidence: list[dict[str, Any]] = []
    for relation in relations:
        graph_evidence.extend(_evidence_items(relation.evidence_json))

    return ProcessGraphDTO(
        process_id=str(process.id),
        process_name=process.canonical_name,
        process_goal=process.goal,
        process_owner=process.owner_candidate,
        inputs=inputs,
        outputs=outputs,
        actions=actions,
        roles=roles,
        systems=systems,
        forms=forms,
        documents=documents,
        resources=resources,
        risks=smk_sections["risks"],
        effectiveness_criteria=smk_sections["effectiveness_criteria"],
        documentation_and_archive=smk_sections["documentation_and_archive"],
        applications=smk_sections["applications"],
        change_registration=smk_sections["change_registration"],
        issue_and_acquaintance=smk_sections["issue_and_acquaintance"],
        storage_locations=_unique_strings(smk_sections["storage_locations"]),
        retention_terms=_unique_strings(smk_sections["retention_terms"]),
        responsible_for_storage=_unique_strings(smk_sections["responsible_for_storage"]),
        measurement_methods=derived.get("measurement_methods", []),
        conditions=_unique_strings(derived["conditions"]),
        subprocesses=subprocesses,
        external_references=external_references,
        evidence=graph_evidence,
        warnings=warnings,
        process_metadata=process_metadata,
    )


def attach_source_document_context(
    graph: ProcessGraphDTO,
    regulating_cards: list[DocumentCard],
) -> ProcessGraphDTO:
    """Обогатить граф типом и уровнем исходного нормативного документа."""
    source_items: list[ProcessSourceDocumentTypeItem] = []
    document_types = []

    for card in regulating_cards:
        if not card.document_type:
            continue
        document_types.append(card.document_type)
        level = DOCUMENT_TYPE_TO_LEVEL.get(card.document_type)
        document_name = card.document_code or card.title or card.file_name
        source_items.append(
            ProcessSourceDocumentTypeItem(
                document_type=card.document_type.value,
                document_type_label=get_document_type_label(card.document_type),
                qms_level=level.value if level else None,
                qms_level_label=get_document_level_label(level),
                document_name=str(document_name).strip() if document_name else None,
            )
        )

    primary = select_primary_document_type(document_types)
    context = document_type_context_fields(primary)
    return graph.model_copy(
        update={
            **context,
            "primary_document_type": primary.value if primary else None,
            "source_document_types": source_items,
        }
    )


class ProcessGraphBuilder:
    """Сборка нормативного графа процесса СМК из ProcessCard, NdRelation и DocumentCard."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def build_process_graph(self, process_id: str) -> ProcessGraphDTO:
        parsed_id = _parse_process_id(process_id)
        logger.info("nd_control.process_graph.start", process_id=str(parsed_id))

        process = await self._load_process(parsed_id)
        relations = await self._load_process_relations(parsed_id)
        logger.info(
            "nd_control.process_graph.relations_loaded",
            process_id=str(parsed_id),
            count=len(relations),
        )

        regulating_documents = await self._load_regulating_documents(parsed_id, relations)
        regulating_cards = await self._load_regulating_document_cards(parsed_id, relations)
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
            regulating_documents=regulating_documents,
        )
        graph = attach_source_document_context(graph, regulating_cards)
        logger.info(
            "nd_control.process_graph.completed",
            process_id=str(parsed_id),
            roles=len(graph.roles),
            actions=len(graph.actions),
            subprocesses=len(graph.subprocesses),
            source_document_type=graph.source_document_type,
        )
        return graph

    async def _load_process(self, process_id: uuid.UUID) -> ProcessCard:
        process = await self.db.get(ProcessCard, process_id)
        if not process:
            raise ProcessGraphBuilderError("Процесс не найден", code="process_not_found")
        return process

    async def _load_process_relations(self, process_id: uuid.UUID) -> list[NdRelation]:
        result = await self.db.execute(
            select(NdRelation).where(
                or_(
                    (NdRelation.source_type == NdGraphEntityType.PROCESS) & (NdRelation.source_id == process_id),
                    (NdRelation.target_type == NdGraphEntityType.PROCESS) & (NdRelation.target_id == process_id),
                )
            )
        )
        return list(result.scalars().all())

    async def _load_regulating_documents(
        self,
        process_id: uuid.UUID,
        relations: list[NdRelation],
    ) -> list[str]:
        document_ids: set[uuid.UUID] = set()
        names: list[str] = []
        for relation in relations:
            if relation.relation_type != NdRelationType.DOCUMENT_REGULATES_PROCESS:
                continue
            if relation.source_type == NdGraphEntityType.DOCUMENT and relation.source_id:
                document_ids.add(relation.source_id)
                names.append(relation.source_name)
        if not document_ids:
            return _unique_strings(names)

        result = await self.db.execute(select(DocumentCard).where(DocumentCard.document_id.in_(document_ids)))
        for card in result.scalars().all():
            label = card.document_code or card.title or card.file_name
            if label:
                names.append(str(label).strip())
        return _unique_strings(names)

    async def _load_regulating_document_cards(
        self,
        process_id: uuid.UUID,
        relations: list[NdRelation],
    ) -> list[DocumentCard]:
        document_ids: set[uuid.UUID] = set()
        for relation in relations:
            if relation.relation_type != NdRelationType.DOCUMENT_REGULATES_PROCESS:
                continue
            if relation.source_type == NdGraphEntityType.DOCUMENT and relation.source_id:
                document_ids.add(relation.source_id)
        if not document_ids:
            return []

        result = await self.db.execute(select(DocumentCard).where(DocumentCard.document_id.in_(document_ids)))
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
    """Адаптер ProcessGraphDTO → контекст для LLM/Mermaid по СТО-34-003."""
    process_graph = graph.model_dump(mode="json")
    return {
        "process_graph": process_graph,
        "process": {
            "id": graph.process_id,
            "name": graph.process_name,
            "goal": graph.process_goal,
            "owner": graph.process_owner,
        },
        "standard_profile": "STO-34-003_GOST-19.701-90",
        "actors": graph.roles,
        "roles": graph.roles,
        "actions": process_graph["actions"],
        "steps": process_graph["actions"],
        "inputs": graph.inputs,
        "outputs": graph.outputs,
        "systems": graph.systems,
        "forms": graph.forms,
        "documents": graph.documents,
        "resources": process_graph["resources"],
        "risks": process_graph["risks"],
        "effectiveness_criteria": process_graph["effectiveness_criteria"],
        "process_metadata": process_graph.get("process_metadata") or {},
        "documentation_and_archive": process_graph["documentation_and_archive"],
        "applications": process_graph["applications"],
        "change_registration": process_graph["change_registration"],
        "issue_and_acquaintance": process_graph["issue_and_acquaintance"],
        "storage_locations": graph.storage_locations,
        "retention_terms": graph.retention_terms,
        "responsible_for_storage": graph.responsible_for_storage,
        "measurement_methods": graph.measurement_methods,
        "conditions": graph.conditions,
        "subprocesses": [item.model_dump(mode="json") for item in graph.subprocesses],
        "external_references": [item.model_dump(mode="json") for item in graph.external_references],
        "warnings": graph.warnings,
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
