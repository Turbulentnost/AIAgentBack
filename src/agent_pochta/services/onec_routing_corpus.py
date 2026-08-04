"""Корпус документов 1С агента для обучения/оценки BGE-маршрутизации."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from agent_pochta.config import PROJECT_ROOT, Settings, get_settings
from agent_pochta.services.odata_client import ODataClient

EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
DEPT_CODE_RE = re.compile(r"^\d{2}-\d{6}$")

DEFAULT_AGENT_RESPONSIBLE_KEY = "a5e55eea-3a0a-11f0-9679-6cb31113810c"


def load_agent_responsible_key(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    path = Path(settings.odata_incoming_defaults_file or PROJECT_ROOT / "data" / "odata_incoming_defaults.json")
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        key = str(data.get("Ответственный_Key") or "").strip()
        if key:
            return key
    return DEFAULT_AGENT_RESPONSIBLE_KEY


def load_guid_maps(
    settings: Settings | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """guid.lower() -> department code; code -> display name."""
    settings = settings or get_settings()
    keys_path = Path(settings.odata_department_keys_file or PROJECT_ROOT / "data" / "odata_department_keys.json")
    if not keys_path.is_absolute():
        keys_path = PROJECT_ROOT / keys_path
    if not keys_path.is_file():
        keys_path = PROJECT_ROOT / "data" / "odata_department_keys.json"
    code_by_guid: dict[str, str] = {}
    if keys_path.is_file():
        raw = json.loads(keys_path.read_text(encoding="utf-8"))
        code_by_guid = {str(v).lower(): str(k) for k, v in raw.items() if v}

    name_by_code: dict[str, str] = {}
    rules_path = PROJECT_ROOT / "data" / "routing_rules.json"
    if rules_path.is_file():
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
        for rule in rules.get("exact_email_rules", []) + rules.get("content_rules", []):
            if rule.get("code") and rule.get("name"):
                name_by_code.setdefault(str(rule["code"]), str(rule["name"]))
        for block in (rules.get("info_strict_rules") or {}).values():
            if isinstance(block, dict) and block.get("code") and block.get("name"):
                name_by_code.setdefault(str(block["code"]), str(block["name"]))

    tz_path = PROJECT_ROOT / "data" / "tz_department_topics.json"
    if tz_path.is_file():
        tz = json.loads(tz_path.read_text(encoding="utf-8"))
        for code, meta in tz.items():
            if not isinstance(meta, dict):
                continue
            if meta.get("topics"):
                name_by_code.setdefault(code, str(meta["topics"][0]))
            elif meta.get("names"):
                name_by_code.setdefault(code, str(meta["names"][0]))

    ui_path = PROJECT_ROOT / "data" / "ui_department_allowlist.json"
    if ui_path.is_file():
        ui = json.loads(ui_path.read_text(encoding="utf-8"))
        for item in ui.get("departments", []):
            if item.get("code") and item.get("name"):
                name_by_code.setdefault(str(item["code"]), str(item["name"]))

    return code_by_guid, name_by_code


def build_agent_docs_filter(*, since: date | datetime | str, responsible_key: str | None = None) -> str:
    if isinstance(since, str):
        since_dt = datetime.fromisoformat(since if "T" in since else f"{since}T00:00:00")
    elif isinstance(since, date) and not isinstance(since, datetime):
        since_dt = datetime.combine(since, datetime.min.time())
    else:
        since_dt = since
    since_literal = since_dt.strftime("%Y-%m-%dT00:00:00")
    key = responsible_key or DEFAULT_AGENT_RESPONSIBLE_KEY
    return (
        f"Ответственный_Key eq guid'{key}' "
        f"and Date ge datetime'{since_literal}'"
    )


def odata_client_from_settings(settings: Settings | None = None) -> ODataClient:
    settings = settings or get_settings()
    return ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120.0,
    )


def fetch_agent_incoming_docs(
    since: date | datetime | str,
    *,
    settings: Settings | None = None,
    page_size: int = 500,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    entity = settings.odata_incoming_doc_entity
    responsible_key = load_agent_responsible_key(settings)
    filter_expr = build_agent_docs_filter(since=since, responsible_key=responsible_key)
    client = odata_client_from_settings(settings)
    rows = client.fetch_filtered(entity, filter_expr=filter_expr, page_size=page_size)
    rows.sort(key=lambda d: str(d.get("Date") or ""), reverse=True)
    if limit is not None:
        return rows[:limit]
    return rows


def resolve_dept_from_1c_doc(
    doc: dict[str, Any],
    *,
    code_by_guid: dict[str, str] | None = None,
    name_by_code: dict[str, str] | None = None,
    guid_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    if code_by_guid is None or name_by_code is None:
        code_by_guid, name_by_code = load_guid_maps()
    guid_names = guid_names or {}

    komu = (doc.get("Кому") or "").strip()
    exec_key = (doc.get("ПодразделениеИсполнитель_Key") or "").strip().lower()
    assignee_key = (doc.get("КомуПодразделениеСсылка_Key") or "").strip().lower()

    code = ""
    source = ""
    if komu and DEPT_CODE_RE.match(komu):
        code = komu
        source = "Кому"
    elif exec_key and exec_key != EMPTY_GUID:
        code = code_by_guid.get(exec_key, "")
        source = "ПодразделениеИсполнитель_Key"
        if not code:
            code = guid_names.get(exec_key, "")
    elif assignee_key and assignee_key != EMPTY_GUID:
        code = code_by_guid.get(assignee_key, "")
        source = "КомуПодразделениеСсылка_Key"
        if not code:
            code = guid_names.get(assignee_key, "")

    if code and not code.startswith("00-") and DEPT_CODE_RE.match(code):
        code = code
    elif code and not code.startswith("00-"):
        mapped = code_by_guid.get(exec_key) or code_by_guid.get(assignee_key)
        if mapped:
            code = mapped

    final_code = code if code.startswith("00-") else ""
    name = name_by_code.get(final_code, "") if final_code else ""
    if not name and final_code:
        name = final_code

    return {
        "department_code": final_code,
        "department_name": name or final_code,
        "destination_source": source or "(нет)",
        "Кому": komu,
    }


def doc_number(doc: dict[str, Any]) -> str:
    return str(doc.get("Number") or "").strip()


def doc_ref_key(doc: dict[str, Any]) -> str:
    return str(doc.get("Ref_Key") or "").strip()
