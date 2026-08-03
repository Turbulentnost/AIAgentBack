"""Find processes for agent spam-marked doc АЛ00-000763."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

DOC_REF = "7e9e71b2-872c-11f1-984b-6cb31113810e"
DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
ENTITIES = (
    "BusinessProcess_Задание",
    "BusinessProcess_CRM_БизнесПроцесс",
    "Task_ЗадачаИсполнителя",
)


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
    ).raise_for_status().json()

    processes = {}
    for entity in ENTITIES:
        flt = f"Предмет eq '{DOC_REF}'"
        url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$top=20"
        resp = httpx.get(url, auth=auth, timeout=120)
        processes[entity] = {
            "status": resp.status_code,
            "items": resp.json().get("value", []) if resp.status_code == 200 else resp.text[:300],
        }

    print(
        json.dumps(
            {
                "doc": {
                    "Number": doc.get("Number"),
                    "Ref_Key": doc.get("Ref_Key"),
                    "БизнесПроцессЗапущен": doc.get("БизнесПроцессЗапущен"),
                    "DeletionMark": doc.get("DeletionMark"),
                    "Статус": doc.get("Статус"),
                },
                "processes": processes,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
