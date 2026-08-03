"""Find BP/tasks linked via email document basis."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

EMAIL_REF = "42a4e148-9aeb-11f0-9710-6cb3111380bc"
ENTITIES = ("BusinessProcess_Задание", "BusinessProcess_CRM_БизнесПроцесс", "Task_ЗадачаИсполнителя")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    report = {}
    for entity in ENTITIES:
        flt = f"Предмет eq '{EMAIL_REF}'"
        url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$top=10"
        resp = httpx.get(url, auth=auth, timeout=120)
        report[entity] = resp.json().get("value", []) if resp.status_code == 200 else resp.text[:200]
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
