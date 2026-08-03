"""DEBUG ONLY: probe volume (tom) storage POST vs IB — не запускать на prod-документах."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

if __name__ == "__main__" and "--i-know-this-creates-probe-files" not in sys.argv:
    print(
        "Refusing to run: pass --i-know-this-creates-probe-files to create PROBE762-* test files.",
        file=sys.stderr,
    )
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.email_msg import eml_bytes_to_msg_bytes  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    AttachedFileInput,
    attach_file_to_incoming_document,
    build_attached_file_payload,
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.odata_integration import resolve_attached_file_author_key  # noqa: E402

DOC762 = "18516943-871f-11f1-984b-6cb31113810e"
VOLUME = "21886495-364e-11ea-82f2-ac1f6b05524c"
AUTHOR = "a5e55eea-3a0a-11f0-9679-6cb31113810c"
ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"


def load_762_eml(client, fm) -> bytes:
    flt = f"ВладелецФайла_Key eq guid'{DOC762}'"
    base = settings.odata_base_url.rstrip("/") + "/"
    url = (
        f"{base}{quote(ENTITY)}?$format=json"
        f"&$filter={quote(flt)}&$orderby=ДатаСоздания desc&$top=5"
    )
    auth = (settings.odata_username, settings.odata_password)
    items = httpx.get(url, auth=auth, timeout=120).json().get("value", [])
    eml_item = next((i for i in items if (i.get("Расширение") or "").lower() == "eml"), items[0])
    return read_attached_file_storage_bytes(
        client, entity=ENTITY, ref_key=eml_item["Ref_Key"], field_map=fm
    )


def patch_editor(client, ref_key: str, author: str) -> dict:
    return client.patch_entity(
        ENTITY,
        ref_key,
        {"Редактирует_Key": author, "Автор_Key": author},
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    global settings
    settings = get_settings()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    fm = load_attached_file_field_map()
    author = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key or AUTHOR,
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )
    eml_bytes = load_762_eml(client, fm)
    msg_bytes = eml_bytes_to_msg_bytes(eml_bytes)
    processed = datetime.now(ZoneInfo("Europe/Moscow"))
    report: dict = {"eml_size": len(eml_bytes), "msg_size": len(msg_bytes)}

    modes = [
        (
            "ib_eml",
            {
                **fm,
                "defaults": {
                    **(fm.get("defaults") or {}),
                    "storage_kind": "ВИнформационнойБазе",
                    "upload_binary_via_stream": False,
                },
            },
            AttachedFileInput(
                filename="PROBE762-IB.eml",
                content=eml_bytes,
                author_key=author,
                edited_by_key=author,
                processed_at=processed,
            ),
        ),
        (
            "tom_eml",
            {
                **fm,
                "defaults": {
                    **(fm.get("defaults") or {}),
                    "storage_kind": "ВТомахНаДиске",
                    "volume_key": settings.odata_file_volume_key or VOLUME,
                    "upload_binary_via_stream": False,
                },
            },
            AttachedFileInput(
                filename="PROBE762-TOM.eml",
                content=eml_bytes,
                author_key=author,
                edited_by_key=author,
                processed_at=processed,
            ),
        ),
        (
            "tom_msg",
            {
                **fm,
                "defaults": {
                    **(fm.get("defaults") or {}),
                    "storage_kind": "ВТомахНаДиске",
                    "volume_key": settings.odata_file_volume_key or VOLUME,
                    "upload_binary_via_stream": False,
                },
            },
            AttachedFileInput(
                filename="PROBE762-TOM.msg",
                content=msg_bytes,
                author_key=author,
                edited_by_key=author,
                processed_at=processed,
            ),
        ),
    ]

    for label, field_map, file_input in modes:
        entry: dict = {"label": label}
        try:
            entity, payload = build_attached_file_payload(
                document_ref_key=DOC762,
                file_input=file_input,
                field_map=field_map,
            )
            entry["payload_keys"] = sorted(payload.keys())
            entry["storage"] = payload.get("ТипХраненияФайла")
            entry["tom"] = payload.get("Том_Key")
            entry["edited_field"] = payload.get("Редактировал_Key")
            result = attach_file_to_incoming_document(
                client,
                document_ref_key=DOC762,
                file_input=file_input,
                field_map=field_map,
                verify_owner_exists=False,
            )
            stored = read_attached_file_storage_bytes(
                client, entity=ENTITY, ref_key=result.ref_key, field_map=field_map
            )
            rec = client.get_by_key(ENTITY, result.ref_key) or {}
            entry["ok"] = True
            entry["ref"] = result.ref_key
            entry["stored_bytes"] = len(stored)
            entry["meta"] = {
                k: rec.get(k)
                for k in (
                    "ТипХраненияФайла",
                    "Том_Key",
                    "ПутьКФайлу",
                    "Размер",
                    "Редактирует_Key",
                    "Редактировал_Key",
                    "ФайлХранилище_Type",
                )
            }
            try:
                patch_editor(client, result.ref_key, author)
                rec2 = client.get_by_key(ENTITY, result.ref_key) or {}
                entry["after_patch"] = {
                    "Редактирует_Key": rec2.get("Редактирует_Key"),
                    "Редактировал_Key": rec2.get("Редактировал_Key"),
                }
            except Exception as patch_exc:
                entry["patch_error"] = str(patch_exc)[:300]
        except Exception as exc:
            entry["ok"] = False
            entry["error"] = str(exc)[:500]
        report[label] = entry

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
