"""Probe CRM business process link to incoming doc."""
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
CRM_BP = "BusinessProcess_CRM_БизнесПроцесс"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)

    ref = "5185c652-9aeb-11f0-9710-6cb3111380bc"
    bp_url = f"{base}{quote(CRM_BP)}?$format=json&$orderby=Date desc&$top=1000"
    items = httpx.get(bp_url, auth=auth, timeout=120).raise_for_status().json().get("value", [])

    matched = []
    for item in items:
        subject_keys = {
            k: item.get(k)
            for k in sorted(item)
            if any(x in k for x in ("Предмет", "Документ", "Основан", "Subject", "Вход", "CRM", "Ref"))
        }
        predmet = item.get("Предмет")
        if predmet == ref or ref in str(subject_keys.values()):
            matched.append(
                {
                    "Ref_Key": item.get("Ref_Key"),
                    "Number": item.get("Number"),
                    "DeletionMark": item.get("DeletionMark"),
                    "Completed": item.get("Completed"),
                    "subject_keys": subject_keys,
                }
            )

    sample = items[0] if items else {}
    print(
        json.dumps(
            {
                "crm_bp_count_top1000": len(items),
                "matched_for_doc": matched,
                "sample_crm_bp_keys": sorted(sample.keys()) if sample else [],
                "sample_subject_keys": {
                    k: sample.get(k)
                    for k in sorted(sample)
                    if any(x in k for x in ("Предмет", "Документ", "Основан", "Subject", "CRM"))
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
