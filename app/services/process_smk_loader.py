from __future__ import annotations

from typing import Any

from app.models.nd_control_structural import ProcessCard
from app.schemas.process_smk_sections import (
    ProcessApplicationItem,
    ProcessChangeRegistrationItem,
    ProcessDocumentationArchiveItem,
    ProcessEffectivenessCriterionItem,
    ProcessIssueAcquaintanceItem,
    ProcessResourceItem,
    ProcessRiskItem,
    evidence_to_dicts,
)


def _load_resources(raw: list | None) -> list[ProcessResourceItem]:
    items: list[ProcessResourceItem] = []
    for entry in raw or []:
        if isinstance(entry, str) and entry.strip():
            items.append(ProcessResourceItem(name=entry.strip()))
            continue
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("title") or "").strip()
        if not name:
            continue
        items.append(
            ProcessResourceItem(
                name=name,
                type=entry.get("type"),
                evidence=evidence_to_dicts(entry.get("evidence")),
            )
        )
    return items


def _load_effectiveness_criteria(raw: list | None) -> list[ProcessEffectivenessCriterionItem]:
    items: list[ProcessEffectivenessCriterionItem] = []
    for entry in raw or []:
        if isinstance(entry, str) and entry.strip():
            items.append(ProcessEffectivenessCriterionItem(name=entry.strip()))
            continue
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("title") or "").strip()
        if not name:
            continue
        items.append(
            ProcessEffectivenessCriterionItem(
                name=name,
                measurement_method=entry.get("measurement_method"),
                reporting_period=entry.get("reporting_period"),
                evidence=evidence_to_dicts(entry.get("evidence")),
            )
        )
    return items


def _load_risks(raw: list | None, action_titles: dict[str, str]) -> list[ProcessRiskItem]:
    items: list[ProcessRiskItem] = []
    for entry in raw or []:
        if isinstance(entry, str) and entry.strip():
            items.append(ProcessRiskItem(risk=entry.strip()))
            continue
        if not isinstance(entry, dict):
            continue
        risk = str(entry.get("risk") or entry.get("name") or entry.get("title") or "").strip()
        if not risk:
            continue
        related_action_title = entry.get("related_action")
        related_action_id = None
        if related_action_title:
            for action_id, title in action_titles.items():
                if title.casefold() == str(related_action_title).casefold():
                    related_action_id = action_id
                    break
                if str(related_action_title).casefold() in title.casefold():
                    related_action_id = action_id
                    break
        items.append(
            ProcessRiskItem(
                risk=risk,
                consequence=entry.get("consequence"),
                control_measure=entry.get("control_measure"),
                responsible=entry.get("responsible"),
                related_action_id=related_action_id,
                related_action_title=str(related_action_title) if related_action_title else None,
                evidence=evidence_to_dicts(entry.get("evidence")),
            )
        )
    return items


def _load_documentation_archive(raw: list | None) -> list[ProcessDocumentationArchiveItem]:
    items: list[ProcessDocumentationArchiveItem] = []
    for entry in raw or []:
        if isinstance(entry, str) and entry.strip():
            items.append(ProcessDocumentationArchiveItem(document=entry.strip()))
            continue
        if not isinstance(entry, dict):
            continue
        document = str(entry.get("document") or entry.get("name") or entry.get("title") or "").strip()
        if not document:
            continue
        items.append(
            ProcessDocumentationArchiveItem(
                document=document,
                storage_place=entry.get("storage_place"),
                responsible=entry.get("responsible"),
                retention_term=entry.get("retention_term"),
                evidence=evidence_to_dicts(entry.get("evidence")),
            )
        )
    return items


def _load_applications(raw: list | None) -> list[ProcessApplicationItem]:
    items: list[ProcessApplicationItem] = []
    for entry in raw or []:
        if isinstance(entry, str) and entry.strip():
            items.append(ProcessApplicationItem(name=entry.strip()))
            continue
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("title") or "").strip()
        if not name:
            continue
        items.append(
            ProcessApplicationItem(
                name=name,
                code=entry.get("code"),
                description=entry.get("description"),
                evidence=evidence_to_dicts(entry.get("evidence")),
            )
        )
    return items


def _load_change_registration(raw: list | None) -> list[ProcessChangeRegistrationItem]:
    items: list[ProcessChangeRegistrationItem] = []
    for entry in raw or []:
        if isinstance(entry, str) and entry.strip():
            items.append(ProcessChangeRegistrationItem(title=entry.strip()))
            continue
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or entry.get("name") or "").strip()
        if not title:
            continue
        items.append(
            ProcessChangeRegistrationItem(
                title=title,
                description=entry.get("description"),
                evidence=evidence_to_dicts(entry.get("evidence")),
            )
        )
    return items


def _load_issue_acquaintance(raw: list | None) -> list[ProcessIssueAcquaintanceItem]:
    items: list[ProcessIssueAcquaintanceItem] = []
    for entry in raw or []:
        if isinstance(entry, str) and entry.strip():
            items.append(ProcessIssueAcquaintanceItem(title=entry.strip()))
            continue
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or entry.get("name") or "").strip()
        if not title:
            continue
        items.append(
            ProcessIssueAcquaintanceItem(
                title=title,
                description=entry.get("description"),
                evidence=evidence_to_dicts(entry.get("evidence")),
            )
        )
    return items


def load_smk_sections_from_process(
    process: ProcessCard,
    *,
    action_titles: dict[str, str],
) -> dict[str, Any]:
    return {
        "resources": _load_resources(process.resources_json),
        "effectiveness_criteria": _load_effectiveness_criteria(process.effectiveness_criteria_json),
        "risks": _load_risks(process.risks_json, action_titles),
        "documentation_and_archive": _load_documentation_archive(process.documentation_and_archive_json),
        "applications": _load_applications(process.applications_json),
        "change_registration": _load_change_registration(process.change_registration_json),
        "issue_and_acquaintance": _load_issue_acquaintance(process.issue_and_acquaintance_json),
        "storage_locations": [str(item).strip() for item in (process.storage_locations_json or []) if str(item).strip()],
        "retention_terms": [str(item).strip() for item in (process.retention_terms_json or []) if str(item).strip()],
        "responsible_for_storage": [
            str(item).strip() for item in (process.responsible_for_storage_json or []) if str(item).strip()
        ],
    }
