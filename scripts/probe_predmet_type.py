"""Inspect Предмет property type in BP metadata."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402


def block(entity: str, meta: str) -> str:
    marker = f'EntityType Name="{entity}"'
    idx = meta.find(marker)
    if idx < 0:
        return ""
    end = meta.find("</EntityType>", idx)
    return meta[idx : end if end >= 0 else idx + 8000]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    meta = httpx.get(f"{base}$metadata", auth=auth, timeout=120).raise_for_status().text

    entities = ("BusinessProcess_Задание", "BusinessProcess_CRM_БизнесПроцесс", "Task_ЗадачаИсполнителя")
    report = {}
    for entity in entities:
        b = block(entity, meta)
        predmet = re.search(r'Property Name="Предмет"[^>]*/>', b)
        predmet_type = re.search(r'Property Name="Предмет_Type"[^>]*/>', b)
        report[entity] = {
            "predmet": predmet.group(0) if predmet else None,
            "predmet_type": predmet_type.group(0) if predmet_type else None,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
