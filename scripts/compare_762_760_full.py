"""Full OData field comparison: 2026 АЛ00-000760 vs АЛ00-000762 attachments."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402

DOCS = {
    "760-2026": "20dbfa4d-8689-11f1-984a-6cb31113810e",
    "762-2026": "18516943-871f-11f1-984b-6cb31113810e",
}
DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
SKIP_DIFF = {"Ref_Key", "Description", "Расширение", "Размер", "ДатаСоздания", "ДатаМодификацииУниверсальная"}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    fm = load_attached_file_field_map()
    entity = fm["entity"]
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    out_dir = ROOT / "data" / "temp" / "compare_762"
    out_dir.mkdir(parents=True, exist_ok=True)

    report: dict = {"documents": {}, "attachments": {}}
    for label, owner in DOCS.items():
        doc_url = f"{base}{quote(DOC_ENTITY)}(guid'{owner}')?$format=json"
        report["documents"][label] = httpx.get(doc_url, auth=auth, timeout=120).json()

        flt = f"ВладелецФайла_Key eq guid'{owner}'"
        url = (
            f"{base}{quote(entity)}?$format=json"
            f"&$filter={quote(flt)}&$orderby=ДатаСоздания desc&$top=20"
        )
        items = httpx.get(url, auth=auth, timeout=120).json().get("value", [])
        files = []
        for item in items:
            ref = item.get("Ref_Key", "")
            content = (
                read_attached_file_storage_bytes(
                    client, entity=entity, ref_key=ref, field_map=fm
                )
                if ref
                else b""
            )
            ext = (item.get("Расширение") or "").strip()
            desc = (item.get("Description") or "").strip()
            fname = f"{desc}.{ext}" if ext else desc
            save_path = out_dir / f"{label}_{fname}"
            if content:
                save_path.write_bytes(content)
            files.append(
                {
                    "ref": ref,
                    "name": fname,
                    "fields": item,
                    "storage_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest() if content else None,
                    "cfb": content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" if content else False,
                    "pdf": content[:5] == b"%PDF-" if content else False,
                    "saved": str(save_path) if content else None,
                }
            )
        report["attachments"][label] = {"owner": owner, "count": len(files), "files": files}

    ok_files = report["attachments"].get("760-2026", {}).get("files", [])
    bad_files = report["attachments"].get("762-2026", {}).get("files", [])
    if ok_files and bad_files:
        ok0 = ok_files[0]["fields"]
        bad0 = bad_files[0]["fields"]
        report["msg_field_diff"] = {
            k: {"760": ok0.get(k), "762": bad0.get(k)}
            for k in sorted(set(ok0) | set(bad0))
            if ok0.get(k) != bad0.get(k) and k not in SKIP_DIFF
        }

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
