"""List all attachments for НП00-003884 with stream details."""
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

OWNER = "a54e387c-868c-11f1-984a-6cb31113810e"
_OLD = "abf67d81-868c-11f1-984a-6cb31113810e"
_NEW = "9f4cf81a-869a-11f1-984a-6cb31113810e"


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

    flt = f"ВладелецФайла_Key eq guid'{OWNER}'"
    url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$orderby=Ref_Key desc"
    with httpx.Client(timeout=120, auth=auth) as hc:
        items = hc.get(url).raise_for_status().json().get("value", [])

    out = []
    for item in items:
        ref = item.get("Ref_Key", "")
        content = read_attached_file_storage_bytes(
            client, entity=entity, ref_key=ref, field_map=fm
        )
        stream_url = f"{base}{quote(entity)}(guid'{ref}')/ФайлХранилище"
        with httpx.Client(timeout=120, auth=auth) as hc:
            sr = hc.get(stream_url)
            direct = sr.content or b""
        out.append(
            {
                "ref_key": ref,
                "label": "new" if ref == _NEW else ("old" if ref == _OLD else "other"),
                "created": item.get("ДатаСоздания"),
                "size_meta": item.get("Размер"),
                "storage_len": len(content),
                "stream_len": len(direct),
                "magic": content[:8].hex() if content else "",
                "author": item.get("Автор_Key"),
                "editor": item.get("Редактировал_Key"),
            }
        )

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
