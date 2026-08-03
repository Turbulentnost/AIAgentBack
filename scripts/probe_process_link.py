"""Find business process link for Document_ТД_ВходящаяКорреспонденция."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    meta = httpx.get(f"{base}$metadata", auth=auth, timeout=120).raise_for_status().text

    doc_marker = f'EntityType Name="{DOC_ENTITY}"'
    idx = meta.find(doc_marker)
    doc_props: list[str] = []
    if idx >= 0:
        end = meta.find("</EntityType>", idx)
        block = meta[idx:end] if end >= 0 else meta[idx : idx + 20000]
        doc_props = re.findall(r'Property Name="([^"]+)"', block)

    bp_doc_props = [p for p in doc_props if "Бизнес" in p or "Процесс" in p or "Задач" in p]

    # Docs with running BP
    flt = "БизнесПроцессЗапущен eq true"
    url = (
        f"{base}{quote(DOC_ENTITY)}?$format=json"
        f"&$filter={quote(flt)}&$orderby=Date desc&$top=5"
    )
    running = httpx.get(url, auth=auth, timeout=120).raise_for_status().json().get("value", [])

    # Probe BusinessProcess_Задание metadata
    bp_marker = 'EntityType Name="BusinessProcess_Задание"'
    bp_idx = meta.find(bp_marker)
    bp_props: list[str] = []
    if bp_idx >= 0:
        end = meta.find("</EntityType>", bp_idx)
        block = meta[bp_idx:end] if end >= 0 else meta[bp_idx : bp_idx + 20000]
        bp_props = re.findall(r'Property Name="([^"]+)"', block)

    bp_subject_props = [
        p
        for p in bp_props
        if any(k in p for k in ("Предмет", "Основан", "Документ", "Subject", "Head", "Головн", "Вход"))
    ]

    # Sample BP records
    bp_samples = []
    for entity in ("BusinessProcess_Задание", "Task_ЗадачаИсполнителя"):
        try:
            u = f"{base}{quote(entity)}?$format=json&$orderby=Date desc&$top=3"
            items = httpx.get(u, auth=auth, timeout=120).raise_for_status().json().get("value", [])
            for item in items:
                bp_samples.append(
                    {
                        "entity": entity,
                        "Ref_Key": item.get("Ref_Key"),
                        "Number": item.get("Number"),
                        "keys": {
                            k: item.get(k)
                            for k in sorted(item)
                            if any(x in k for x in ("Предмет", "Основан", "Документ", "Head", "Subject", "Вход", "Ref"))
                        },
                    }
                )
        except Exception as exc:  # noqa: BLE001
            bp_samples.append({"entity": entity, "error": str(exc)})

    report = {
        "doc_bp_props": bp_doc_props,
        "running_docs": [
            {
                "Ref_Key": d.get("Ref_Key"),
                "Number": d.get("Number"),
                "БизнесПроцессЗапущен": d.get("БизнесПроцессЗапущен"),
                "bp_keys": {k: d.get(k) for k in sorted(d) if "Бизнес" in k or "Процесс" in k or "Задач" in k},
            }
            for d in running
        ],
        "bp_задание_subject_props": bp_subject_props,
        "bp_samples": bp_samples[:10],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
