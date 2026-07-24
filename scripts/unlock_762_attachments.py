"""Unlock OData attachments stuck with Редактирует_Key (file edit lock)."""
from __future__ import annotations

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
    release_attached_file_edit_lock,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402

EMPTY = "00000000-0000-0000-0000-000000000000"
DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
DOC_NUMBER = "АЛ00-000762"


def newest_doc_ref(base: str, auth, number: str) -> str | None:
    url = (
        f"{base}{quote(DOC_ENTITY)}?$format=json"
        f"&$filter={quote(f'Number eq {chr(39)}{number}{chr(39)}')}"
        f"&$orderby=Date desc&$top=1"
    )
    items = httpx.get(url, auth=auth, timeout=60).json().get("value", [])
    return items[0].get("Ref_Key") if items else None


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
    owner = newest_doc_ref(base, auth, DOC_NUMBER)
    if not owner:
        print(json.dumps({"error": f"document {DOC_NUMBER} not found"}, ensure_ascii=False))
        raise SystemExit(1)

    flt = f"ВладелецФайла_Key eq guid'{owner}'"
    url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$top=50"
    items = httpx.get(url, auth=auth, timeout=120).json().get("value", [])
    unlocked = []
    for item in items:
        ref = item.get("Ref_Key", "")
        editor = (item.get("Редактирует_Key") or "").strip()
        if not ref or not editor or editor == EMPTY:
            continue
        release_attached_file_edit_lock(
            client, entity=entity, ref_key=ref, field_map=fm
        )
        check_url = f"{base}{quote(entity)}(guid'{ref}')?$format=json&$select=Ref_Key,Description,Расширение,Редактирует_Key,DeletionMark"
        after = httpx.get(check_url, auth=auth, timeout=60).json()
        unlocked.append(
            {
                "ref": ref,
                "name": f"{item.get('Description')}.{item.get('Расширение')}",
                "editor_before": editor,
                "editor_after": after.get("Редактирует_Key"),
                "deletion_mark": after.get("DeletionMark"),
            }
        )

    print(json.dumps({"doc": DOC_NUMBER, "owner": owner, "unlocked": unlocked}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
