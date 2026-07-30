"""Test string filter on Предмет for business processes."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
SUBJECT_TYPE = f"StandardODATA.{DOC_ENTITY}"


def query(base: str, auth: tuple[str, str], entity: str, doc_ref: str) -> dict:
    flt = f"Предмет eq '{doc_ref}'"
    url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$top=20"
    response = httpx.get(url, auth=auth, timeout=120)
    if response.status_code >= 400:
        return {"entity": entity, "status": response.status_code, "error": response.text[:400]}
    items = response.json().get("value", [])
    return {
        "entity": entity,
        "count": len(items),
        "items": [
            {
                "Ref_Key": i.get("Ref_Key"),
                "Number": i.get("Number"),
                "Предмет_Type": i.get("Предмет_Type"),
                "Completed": i.get("Completed"),
                "DeletionMark": i.get("DeletionMark"),
                "Started": i.get("Started"),
            }
            for i in items
        ],
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)

    # Use a recent agent doc if available from DB, else known running doc
    refs = ["18516943-871f-11f1-984b-6cb31113810e", "5185c652-9aeb-11f0-9710-6cb3111380bc"]
    entities = (
        "BusinessProcess_Задание",
        "BusinessProcess_CRM_БизнесПроцесс",
        "Task_ЗадачаИсполнителя",
    )
    report = []
    for ref in refs:
        for entity in entities:
            report.append({"doc_ref": ref, **query(base, auth, entity, ref)})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
