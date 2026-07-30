"""Read-only: list and parse НП00-003877.msg records from 1C."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings
from agent_pochta.services.odata_attached_file import load_attached_file_field_map, read_attached_file_storage_bytes
from agent_pochta.services.odata_client import ODataClient

REF = "fdb2cd68-8669-11f1-984a-6cb31113810e"
DOC = "НП00-003877"
ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"


def main() -> None:
    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    fm = load_attached_file_field_map()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    flt = f"ВладелецФайла_Key eq guid'{REF}'"
    url = f"{base}{quote(ENTITY)}?$format=json&$filter={quote(flt)}&$orderby=Ref_Key desc"
    items = httpx.get(url, auth=auth, timeout=60).json()["value"]
    targets = [
        i
        for i in items
        if i.get("Description") == DOC and (i.get("Расширение") or "").lower() == "msg"
    ]
    from aspose.email_foss import msg as msgmod

    out_dir = ROOT / "data" / "temp" / "verify_attach"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in targets:
        ref = item["Ref_Key"]
        content = read_attached_file_storage_bytes(client, entity=ENTITY, ref_key=ref, field_map=fm)
        path = out_dir / f"{DOC}_{ref[:8]}.msg"
        path.write_bytes(content)
        m = msgmod.MapiMessage.from_file(str(path))
        sender = getattr(m, "sender_email_address", None) or getattr(m, "sender_name", None)
        if sender is None and getattr(m, "sender", None) is not None:
            sender = str(m.sender)
        rows.append(
            {
                "ref_key": ref,
                "size_bytes": len(content),
                "size_meta": item.get("Размер"),
                "created_at": item.get("ДатаСоздания"),
                "modified_utc": item.get("ДатаМодификацииУниверсальная"),
                "author_key": item.get("Автор_Key"),
                "editor_key": item.get("Редактировал_Key"),
                "saved_path": str(path),
                "magic": content[:8].hex(),
                "hex_first16": content[:16].hex(),
                "subject": getattr(m, "subject", "") or "",
                "from": str(sender or ""),
                "date": str(
                    getattr(m, "delivery_time", None)
                    or getattr(m, "client_submit_time", None)
                    or ""
                ),
            }
        )
    print(json.dumps({"msg_count": len(rows), "records": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
