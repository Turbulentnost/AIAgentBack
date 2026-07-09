"""Импорт справочников RAG из JSON / OData (раздел 9 ТЗ)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_pochta.schemas import Contractor, Department

DEFAULT_KEYWORDS_FILE = Path(__file__).resolve().parents[3] / "data" / "rag_department_keywords.json"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace(";", ",").split(",")]
        return [p for p in parts if p]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def load_department_keywords(path: Path | None = None) -> dict[str, list[str]]:
    """Дополнительные keywords по department_id (не приходят из 1С)."""
    file_path = path or DEFAULT_KEYWORDS_FILE
    if not file_path.is_file():
        return {}
    data = json.loads(file_path.read_text(encoding="utf-8"))
    return {str(k): _as_list(v) for k, v in data.items()}


def merge_department_keywords(
    departments: list[Department],
    extra: dict[str, list[str]],
) -> list[Department]:
    merged: list[Department] = []
    for dept in departments:
        add = extra.get(dept.department_id, [])
        keywords = list(dict.fromkeys([*dept.keywords, *add]))
        merged.append(dept.model_copy(update={"keywords": keywords}))
    return merged


def parse_contractor(raw: dict[str, Any]) -> Contractor | None:
    emails = _as_list(raw.get("emails") or raw.get("email") or raw.get("Email"))
    if not emails:
        return None
    contractor_id = str(
        raw.get("contractor_id") or raw.get("Ref_Key") or raw.get("Code") or emails[0]
    )
    return Contractor(
        contractor_id=contractor_id,
        name=str(raw.get("name") or raw.get("Description") or raw.get("name_full") or contractor_id),
        emails=emails,
        department_codes=_as_list(
            raw.get("department_codes") or raw.get("DepartmentCodes") or raw.get("allowed_departments")
        ),
        contractor_type=str(raw.get("contractor_type") or raw.get("ContractorType") or "клиент"),
    )


def parse_department(raw: dict[str, Any]) -> Department | None:
    department_id = str(raw.get("department_id") or raw.get("Ref_Key") or raw.get("Code") or "")
    if not department_id:
        return None
    return Department(
        department_id=department_id,
        department_name=str(
            raw.get("department_name") or raw.get("Description") or raw.get("name") or department_id
        ),
        head_name=str(raw.get("head_name") or raw.get("HeadName") or raw.get("manager") or ""),
        responsibility=str(raw.get("responsibility") or raw.get("Responsibility") or ""),
        keywords=_as_list(raw.get("keywords") or raw.get("Keywords")),
    )


def load_catalog_from_json(path: Path) -> tuple[list[Contractor], list[Department]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    contractors: list[Contractor] = []
    for row in data.get("contractors") or []:
        if not isinstance(row, dict):
            continue
        item = parse_contractor(row)
        if item:
            contractors.append(item)

    departments: list[Department] = []
    for row in data.get("departments") or []:
        if not isinstance(row, dict):
            continue
        item = parse_department(row)
        if item:
            departments.append(item)
    return contractors, departments


def odata_rows_to_contractors(rows: list[dict[str, Any]]) -> list[Contractor]:
    result: list[Contractor] = []
    for row in rows:
        item = parse_contractor(row)
        if item:
            result.append(item)
    return result


def odata_rows_to_departments(rows: list[dict[str, Any]]) -> list[Department]:
    result: list[Department] = []
    for row in rows:
        item = parse_department(row)
        if item:
            result.append(item)
    return result
