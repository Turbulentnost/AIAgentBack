"""Verify latest 762 attachments after retry."""
from __future__ import annotations

import json
import sys
from urllib.parse import quote

import httpx

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings
from agent_pochta.services.odata_attached_file import (
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient

REFS = [
    "7fa809d2-8723-11f1-984b-6cb31113810e",
    "7fa80b17-8723-11f1-984b-6cb31113810e",
]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    fm = load_attached_file_field_map()
    entity = fm["entity"]
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    report = []
    for ref in REFS:
        url = f"{base}{quote(entity)}(guid'{ref}')?$format=json"
        item = httpx.get(url, auth=auth, timeout=60).raise_for_status().json()
        content = read_attached_file_storage_bytes(
            client, entity=entity, ref_key=ref, field_map=fm
        )
        report.append(
            {
                "ref": ref,
                "desc": item.get("Description"),
                "ext": item.get("Расширение"),
                "size_bytes": len(content),
                "meta_size": item.get("Размер"),
                "author": item.get("Автор_Key"),
                "editor": item.get("Редактирует_Key"),
                "storage": item.get("ТипХраненияФайла"),
                "pdf_magic": content[:5] == b"%PDF-" if content else False,
            }
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
