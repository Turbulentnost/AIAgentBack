"""Trace process/task linkage for an older incoming doc with tasks."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

DOC_REF = "f7e38d5c-421b-11e8-8272-ac1f6b05524d"
DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
SUBJECT_TYPE = f"StandardODATA.{DOC_ENTITY}"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)

    doc = httpx.get(
        f"{base}{quote(DOC_ENTITY)}(guid'{DOC_REF}')?$format=json",
        auth=auth,
        timeout=120,
    )
    flt = f"Предмет eq '{DOC_REF}' and Предмет_Type eq '{SUBJECT_TYPE}'"
    tasks = httpx.get(
        f"{base}{quote('Task_ЗадачаИсполнителя')}?$format=json&$filter={quote(flt)}&$top=20",
        auth=auth,
        timeout=120,
    )
    bp_flt = f"Предмет eq '{DOC_REF}'"
    bp = httpx.get(
        f"{base}{quote('BusinessProcess_Задание')}?$format=json&$filter={quote(bp_flt)}&$top=20",
        auth=auth,
        timeout=120,
    )
    crm = httpx.get(
        f"{base}{quote('BusinessProcess_CRM_БизнесПроцесс')}?$format=json&$filter={quote(bp_flt)}&$top=20",
        auth=auth,
        timeout=120,
    )

    print(
        json.dumps(
            {
                "doc_status": doc.status_code,
                "doc": doc.json() if doc.status_code == 200 else doc.text[:200],
                "tasks": tasks.json().get("value", []) if tasks.status_code == 200 else tasks.text[:200],
                "bp_zadanie": bp.json().get("value", []) if bp.status_code == 200 else bp.text[:200],
                "bp_crm": crm.json().get("value", []) if crm.status_code == 200 else crm.text[:200],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
