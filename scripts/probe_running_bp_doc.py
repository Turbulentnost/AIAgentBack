"""Probe BP/tasks for doc with БизнесПроцессЗапущен=true."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

DOC_REF = "5185c652-9aeb-11f0-9710-6cb3111380bc"
ENTITIES = ("BusinessProcess_Задание", "BusinessProcess_CRM_БизнесПроцесс", "Task_ЗадачаИсполнителя")


def query(base: str, auth: tuple[str, str], entity: str) -> list[dict]:
    flt = f"Предмет eq '{DOC_REF}'"
    url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$top=20"
    resp = httpx.get(url, auth=auth, timeout=120)
    if resp.status_code != 200:
        return [{"error": resp.text[:300]}]
    return resp.json().get("value", [])


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    doc = httpx.get(
        f"{base}{quote('Document_ТД_ВходящаяКорреспонденция')}(guid'{DOC_REF}')?$format=json",
        auth=auth,
        timeout=120,
    ).json()
    report = {"doc": {"Number": doc.get("Number"), "БизнесПроцессЗапущен": doc.get("БизнесПроцессЗапущен"), "Статус": doc.get("Статус")}}
    for entity in ENTITIES:
        items = query(base, auth, entity)
        report[entity] = [
            {
                "Ref_Key": i.get("Ref_Key"),
                "Number": i.get("Number"),
                "Предмет_Type": i.get("Предмет_Type"),
                "HeadTask": i.get("HeadTask"),
                "HeadTask_Type": i.get("HeadTask_Type"),
                "Completed": i.get("Completed"),
                "DeletionMark": i.get("DeletionMark"),
            }
            for i in items
            if "error" not in i
        ] or items
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
