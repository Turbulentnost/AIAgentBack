"""Probe OData metadata for business process entities linked to incoming docs."""
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

    bp = sorted(set(re.findall(r'EntitySet Name="(BusinessProcess[^"]+)"', meta)))
    tasks = sorted(set(re.findall(r'EntitySet Name="(Task[^"]+)"', meta)))
    td_entities = sorted(
        set(re.findall(r'EntitySet Name="([^"]*ТД[^"]*)"', meta))
    )

    doc_marker = f'EntityType Name="{DOC_ENTITY}"'
    idx = meta.find(doc_marker)
    doc_props: list[str] = []
    if idx >= 0:
        end = meta.find("</EntityType>", idx)
        block = meta[idx:end] if end >= 0 else meta[idx : idx + 12000]
        doc_props = re.findall(r'Property Name="([^"]+)"', block)

    process_props = [
        p
        for p in doc_props
        if any(k in p for k in ("Process", "process", "Процесс", "процесс", "Задач", "Business"))
    ]

    # Sample one recent document
    sample_doc: dict | None = None
    url = f"{base}{quote(DOC_ENTITY)}?$format=json&$orderby=Date desc&$top=1"
    items = httpx.get(url, auth=auth, timeout=120).raise_for_status().json().get("value", [])
    if items:
        sample_doc = items[0]

    report = {
        "business_process_entities": bp,
        "task_entities": tasks[:40],
        "td_entities": td_entities,
        "doc_process_props": process_props,
        "sample_doc_keys_with_process": (
            {k: sample_doc.get(k) for k in sorted(sample_doc) if any(x in k for x in ("Process", "Проц", "Задач", "Business"))}
            if sample_doc
            else {}
        ),
        "sample_doc_number": sample_doc.get("Number") if sample_doc else None,
        "sample_doc_ref": sample_doc.get("Ref_Key") if sample_doc else None,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
