"""Inspect incoming doc OData fields and navigation for business process link."""
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
    nav_props: list[str] = []
    props: list[str] = []
    if idx >= 0:
        end = meta.find("</EntityType>", idx)
        block = meta[idx:end] if end >= 0 else meta[idx : idx + 30000]
        nav_props = re.findall(r'NavigationProperty Name="([^"]+)"', block)
        props = re.findall(r'Property Name="([^"]+)"', block)

    ref = "5185c652-9aeb-11f0-9710-6cb3111380bc"
    doc_url = f"{base}{quote(DOC_ENTITY)}(guid'{ref}')?$format=json"
    doc = httpx.get(doc_url, auth=auth, timeout=120).raise_for_status().json()

    # Try navigation properties that look process-related
    nav_samples = {}
    for name in nav_props:
        if any(k in name.lower() for k in ("process", "проц", "task", "задач", "business", "biz")):
            try:
                u = f"{base}{quote(DOC_ENTITY)}(guid'{ref}')/{quote(name)}?$format=json"
                resp = httpx.get(u, auth=auth, timeout=120)
                nav_samples[name] = {
                    "status": resp.status_code,
                    "body": resp.json() if resp.status_code == 200 else resp.text[:300],
                }
            except Exception as exc:  # noqa: BLE001
                nav_samples[name] = {"error": str(exc)}

    # Scan recent BP for incoming docs (client-side match)
    bp_url = f"{base}{quote('BusinessProcess_Задание')}?$format=json&$orderby=Date desc&$top=500"
    bp_items = httpx.get(bp_url, auth=auth, timeout=120).raise_for_status().json().get("value", [])
    matched = [
        {
            "Ref_Key": p.get("Ref_Key"),
            "Number": p.get("Number"),
            "Предмет": p.get("Предмет"),
            "Предмет_Type": p.get("Предмет_Type"),
            "Completed": p.get("Completed"),
            "DeletionMark": p.get("DeletionMark"),
        }
        for p in bp_items
        if p.get("Предмет") == ref
        or (p.get("Предмет_Type") or "").endswith(DOC_ENTITY) and p.get("Предмет") == ref
    ]

    report = {
        "nav_props": nav_props,
        "processish_props": [p for p in props if any(k in p for k in ("Бизнес", "Проц", "Задач"))],
        "doc_processish_values": {k: doc.get(k) for k in props if any(k in x for x in ("Бизнес", "Проц", "Задач"))},
        "nav_samples": nav_samples,
        "matched_bp_for_doc": matched[:10],
        "matched_bp_total_in_top500": len(matched),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
