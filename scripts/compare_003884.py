"""Compare attachment metadata: working vs 003884 vs broken."""
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
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402

DOCS = [
    ("НП00-003822", "c66512e2-85c4-11f1-9849-6cb31113810e", "working-eml"),
    ("НП00-003877", "fdb2cd68-8669-11f1-984a-6cb31113810e", "working-msg"),
    ("НП00-003884", "a54e387c-868c-11f1-984a-6cb31113810e", "reported-broken"),
    ("НП00-003876", "e9e1b18c-8669-11f1-984a-6cb31113810e", "broken"),
]


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
        timeout_sec=settings.odata_timeout_sec,
    )

    out = []
    for doc, owner, label in DOCS:
        flt = f"ВладелецФайла_Key eq guid'{owner}'"
        url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$orderby=Ref_Key desc"
        with httpx.Client(timeout=120, auth=auth) as hc:
            items = hc.get(url).raise_for_status().json().get("value", [])
        target = next(
            (i for i in items if (i.get("Description") or "").strip() == doc),
            items[0] if items else {},
        )
        ref = target.get("Ref_Key", "")
        content = (
            read_attached_file_storage_bytes(
                client, entity=entity, ref_key=ref, field_map=fm
            )
            if ref
            else b""
        )
        out.append(
            {
                "label": label,
                "doc": doc,
                "ref": ref,
                "size_meta": target.get("Размер"),
                "stream_len": len(content),
                "magic": content[:8].hex() if content else "",
                "storage": target.get("ТипХраненияФайла"),
                "storage_type": target.get("ФайлХранилище_Type"),
                "author": target.get("Автор_Key"),
                "editor": target.get("Редактировал_Key"),
                "created": target.get("ДатаСоздания"),
            }
        )

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
