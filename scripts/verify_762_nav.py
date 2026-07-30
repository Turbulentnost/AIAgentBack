"""Verify navigation refs and MSG openability for 762 vs 760."""
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

ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
CASES = {
    "760-ok": "27997dc5-8689-11f1-984a-6cb31113810e",
    "762-cur": "ba6972cd-8727-11f1-984b-6cb31113810e",
}
AUTHOR = "a5e55eea-3a0a-11f0-9679-6cb31113810c"


def nav_get(base: str, auth, attach_ref: str, nav: str) -> dict:
    url = f"{base}{quote(ENTITY)}(guid'{attach_ref}')/{quote(nav)}?$format=json"
    resp = httpx.get(url, auth=auth, timeout=60)
    return {"status": resp.status_code, "body": resp.json() if resp.status_code == 200 else resp.text[:300]}


def author_lookup(base: str, auth, author_key: str) -> dict:
    for entity in (
        "Catalog_Пользователи",
        "Catalog_ПользователиOData",
        "Catalog_Пользователи1С",
    ):
        url = f"{base}{quote(entity)}(guid'{author_key}')?$format=json"
        resp = httpx.get(url, auth=auth, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "entity": entity,
                "status": 200,
                "Description": data.get("Description"),
                "DeletionMark": data.get("DeletionMark"),
            }
    return {"status": "not_found"}


def msg_info(content: bytes) -> dict:
    out_dir = ROOT / "data" / "temp" / "compare_762"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "_nav_probe.msg"
    path.write_bytes(content)
    try:
        from aspose.email_foss import msg as msgmod

        m = msgmod.MapiMessage.from_file(str(path))
        atts = []
        for i, a in enumerate(getattr(m, "attachments", None) or []):
            data = getattr(a, "content_stream", None) or b""
            atts.append(
                {
                    "i": i,
                    "name": getattr(a, "display_name", None) or getattr(a, "long_file_name", None),
                    "mime": getattr(a, "mime_tag", None),
                    "size": len(data),
                }
            )
        return {
            "subject": getattr(m, "subject", None),
            "message_class": getattr(m, "message_class", None),
            "attachments": atts,
        }
    except Exception as exc:
        return {"error": str(exc)}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

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

    report = {"author_lookup": author_lookup(base, auth, AUTHOR), "attachments": {}}
    for label, ref in CASES.items():
        content = read_attached_file_storage_bytes(
            client, entity=ENTITY, ref_key=ref, field_map=fm
        )
        report["attachments"][label] = {
            "ref": ref,
            "bytes": len(content),
            "cfb": content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
            "nav_Автор": nav_get(base, auth, ref, "Автор"),
            "nav_ВладелецФайла": nav_get(base, auth, ref, "ВладелецФайла"),
            "nav_Редактирует": nav_get(base, auth, ref, "Редактирует"),
            "nav_Изменил": nav_get(base, auth, ref, "Изменил"),
            "msg": msg_info(content),
        }

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
