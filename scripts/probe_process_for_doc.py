"""Find BusinessProcess_Задание linked to a specific incoming document."""
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
BP_ENTITY = "BusinessProcess_Задание"
SUBJECT_TYPE = f"StandardODATA.{DOC_ENTITY}"


def find_processes_for_doc(base: str, auth: tuple[str, str], doc_ref: str) -> tuple[list[dict], str | None]:
    flt = f"Предмет eq guid'{doc_ref}'"
    url = (
        f"{base}{quote(BP_ENTITY)}?$format=json"
        f"&$filter={quote(flt)}&$top=20"
    )
    response = httpx.get(url, auth=auth, timeout=120)
    if response.status_code >= 400:
        return [], response.text[:500]
    items = response.json().get("value", [])
    return [
        item
        for item in items
        if item.get("Предмет_Type") in (SUBJECT_TYPE, DOC_ENTITY, None)
        or (item.get("Предмет_Type") or "").endswith(DOC_ENTITY)
    ], None


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)

    flt = "БизнесПроцессЗапущен eq true"
    url = (
        f"{base}{quote(DOC_ENTITY)}?$format=json"
        f"&$filter={quote(flt)}&$orderby=Date desc&$top=3"
    )
    docs = httpx.get(url, auth=auth, timeout=120).raise_for_status().json().get("value", [])

    report = []
    for doc in docs:
        ref = doc.get("Ref_Key", "")
        processes, error = find_processes_for_doc(base, auth, ref)
        report.append(
            {
                "doc_number": doc.get("Number"),
                "doc_ref": ref,
                "filter_error": error,
                "process_count": len(processes),
                "processes": [
                    {
                        "Ref_Key": p.get("Ref_Key"),
                        "Number": p.get("Number"),
                        "Started": p.get("Started"),
                        "Completed": p.get("Completed"),
                        "DeletionMark": p.get("DeletionMark"),
                        "Статус": p.get("Статус"),
                        "Завершен": p.get("Завершен"),
                    }
                    for p in processes
                ],
            }
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
