from __future__ import annotations

from app.schemas.nd_document_extraction import (
    ActionExtraction,
    DocumentExtractionResult,
    DocumentMetaExtraction,
    DocumentScopeExtraction,
    FormExtraction,
    Participant,
    ParticipantsExtraction,
    ProcessExtraction,
    ResponsibilityExtraction,
    UnknownItem,
)


def _norm(value: str | None) -> str:
    return (value or "").strip().casefold()


def _merge_optional_str(current: str | None, incoming: str | None) -> str | None:
    if incoming and incoming.strip():
        if not current or len(incoming.strip()) > len(current.strip()):
            return incoming.strip()
        return current
    return current


def _merge_unique_strings(items: list[str], extra: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for value in [*items, *extra]:
        normalized = value.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged


def _merge_participants(current: list[Participant], incoming: list[Participant]) -> list[Participant]:
    merged = list(current)
    keys = {
        (_norm(p.name), _norm(p.role), _norm(p.department), _norm(p.date))
        for p in current
    }
    for participant in incoming:
        key = (
            _norm(participant.name),
            _norm(participant.role),
            _norm(participant.department),
            _norm(participant.date),
        )
        if key in keys:
            continue
        keys.add(key)
        merged.append(participant)
    return merged


def _merge_participants_block(
    current: ParticipantsExtraction,
    incoming: ParticipantsExtraction,
) -> ParticipantsExtraction:
    return ParticipantsExtraction(
        developed_by=_merge_participants(current.developed_by, incoming.developed_by),
        checked_by=_merge_participants(current.checked_by, incoming.checked_by),
        approved_by=_merge_participants(current.approved_by, incoming.approved_by),
        agreed_by=_merge_participants(current.agreed_by, incoming.agreed_by),
    )


def _merge_actions(current: list[ActionExtraction], incoming: list[ActionExtraction]) -> list[ActionExtraction]:
    merged = list(current)
    keys = {_norm(action.action) for action in current}
    for action in incoming:
        key = _norm(action.action)
        if key in keys:
            continue
        keys.add(key)
        merged.append(action)
    return merged


def _merge_processes(current: list[ProcessExtraction], incoming: list[ProcessExtraction]) -> list[ProcessExtraction]:
    by_name: dict[str, ProcessExtraction] = {_norm(process.name): process for process in current}
    for process in incoming:
        key = _norm(process.name)
        if key not in by_name:
            by_name[key] = process
            continue
        existing = by_name[key]
        by_name[key] = ProcessExtraction(
            name=existing.name,
            description=_merge_optional_str(existing.description, process.description),
            goal=_merge_optional_str(existing.goal, process.goal),
            inputs=_merge_unique_strings(existing.inputs, process.inputs),
            outputs=_merge_unique_strings(existing.outputs, process.outputs),
            actions=_merge_actions(existing.actions, process.actions),
            roles=_merge_unique_strings(existing.roles, process.roles),
            forms=_merge_unique_strings(existing.forms, process.forms),
            systems=_merge_unique_strings(existing.systems, process.systems),
            resources=_merge_unique_strings(existing.resources, process.resources),
            related_departments=_merge_unique_strings(existing.related_departments, process.related_departments),
            owner_candidates=[*existing.owner_candidates, *process.owner_candidates],
        )
    return list(by_name.values())


def _merge_responsibilities(
    current: list[ResponsibilityExtraction],
    incoming: list[ResponsibilityExtraction],
) -> list[ResponsibilityExtraction]:
    merged = list(current)
    keys = {(_norm(item.subject), _norm(item.responsibility), item.role_type) for item in current}
    for item in incoming:
        key = (_norm(item.subject), _norm(item.responsibility), item.role_type)
        if key in keys:
            continue
        keys.add(key)
        merged.append(item)
    return merged


def _merge_forms(current: list[FormExtraction], incoming: list[FormExtraction]) -> list[FormExtraction]:
    merged = list(current)
    keys = {(_norm(form.name), _norm(form.code)) for form in current}
    for form in incoming:
        key = (_norm(form.name), _norm(form.code))
        if key in keys:
            continue
        keys.add(key)
        merged.append(form)
    return merged


def _merge_unknowns(current: list[UnknownItem], incoming: list[UnknownItem]) -> list[UnknownItem]:
    merged = list(current)
    keys = {(item.field, item.reason, _norm(item.description)) for item in current}
    for item in incoming:
        key = (item.field, item.reason, _norm(item.description))
        if key in keys:
            continue
        keys.add(key)
        merged.append(item)
    return merged


def _merge_document_meta(current: DocumentMetaExtraction, incoming: DocumentMetaExtraction) -> DocumentMetaExtraction:
    scope = DocumentScopeExtraction(
        text=_merge_optional_str(current.scope.text, incoming.scope.text),
        departments=_merge_unique_strings(current.scope.departments, incoming.scope.departments),
        positions=_merge_unique_strings(current.scope.positions, incoming.scope.positions),
        applies_to_all_company=current.scope.applies_to_all_company or incoming.scope.applies_to_all_company,
    )
    return DocumentMetaExtraction(
        document_code=_merge_optional_str(current.document_code, incoming.document_code),
        title=_merge_optional_str(current.title, incoming.title),
        document_type=_merge_optional_str(current.document_type, incoming.document_type),
        version=_merge_optional_str(current.version, incoming.version),
        status=_merge_optional_str(current.status, incoming.status),
        approval_date=_merge_optional_str(current.approval_date, incoming.approval_date),
        effective_date=_merge_optional_str(current.effective_date, incoming.effective_date),
        purpose=_merge_optional_str(current.purpose, incoming.purpose),
        scope=scope,
    )


def merge_document_extraction_results(results: list[DocumentExtractionResult]) -> DocumentExtractionResult:
    if not results:
        return DocumentExtractionResult(document=DocumentMetaExtraction())

    merged = results[0]
    for partial in results[1:]:
        merged = DocumentExtractionResult(
            document=_merge_document_meta(merged.document, partial.document),
            participants=_merge_participants_block(merged.participants, partial.participants),
            processes=_merge_processes(merged.processes, partial.processes),
            responsibilities=_merge_responsibilities(merged.responsibilities, partial.responsibilities),
            forms=_merge_forms(merged.forms, partial.forms),
            related_departments=_merge_unique_strings(merged.related_departments, partial.related_departments),
            related_documents=_merge_unique_strings(merged.related_documents, partial.related_documents),
            related_systems=_merge_unique_strings(merged.related_systems, partial.related_systems),
            unknowns=_merge_unknowns(merged.unknowns, partial.unknowns),
        )
    return merged
