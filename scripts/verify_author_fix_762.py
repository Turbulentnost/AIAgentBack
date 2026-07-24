"""Reattach single MSG on АЛ00-000762 to verify Изменил_Key fix."""
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
    AttachedFileInput,
    attach_file_to_incoming_document,
    load_attached_file_field_map,
    now_attached_file_processed_at,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.odata_integration import resolve_attached_file_author_key  # noqa: E402

DOC_REF = "18516943-871f-11f1-984b-6cb31113810e"
OLD_MSG_REF = "07a33c90-8757-11f1-984c-6cb31113810e"
MANUAL_REF = "598a6fa7-8759-11f1-984c-6cb31113810e"
ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
COMPARE_FIELDS = [
    "Ref_Key",
    "Description",
    "Расширение",
    "Автор_Key",
    "Изменил_Key",
    "Редактирует_Key",
    "ТипХраненияФайла",
    "Размер",
]


def nav_modified_by(base: str, auth, ref: str) -> dict:
    url = f"{base}{quote(ENTITY)}(guid'{ref}')/Изменил?$format=json"
    resp = httpx.get(url, auth=auth, timeout=60)
    if resp.status_code == 200:
        data = resp.json()
        return {"status": 200, "Description": data.get("Description")}
    return {"status": resp.status_code}


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

    author = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key or "",
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )
    if not author:
        print(json.dumps({"error": "ODATA_FILE_AUTHOR_KEY not configured"}, ensure_ascii=False))
        raise SystemExit(1)

    old = client.get_by_key(ENTITY, OLD_MSG_REF) or {}
    msg_path = ROOT / "data" / "temp" / "compare_762" / "АЛ00-000762_АЛ00-000762.msg"
    if not msg_path.is_file():
        print(json.dumps({"error": f"local MSG not found: {msg_path}"}, ensure_ascii=False))
        raise SystemExit(1)
    msg_bytes = msg_path.read_bytes()

    delete_entity = getattr(client, "delete_entity", None)
    if callable(delete_entity):
        delete_entity(ENTITY, OLD_MSG_REF)

    result = attach_file_to_incoming_document(
        client,
        document_ref_key=DOC_REF,
        file_input=AttachedFileInput(
            filename="АЛ00-000762.msg",
            content=msg_bytes,
            author_key=author,
            edited_by_key=author,
            processed_at=now_attached_file_processed_at(),
        ),
        field_map=fm,
        verify_owner_exists=False,
    )

    new_record = client.get_by_key(ENTITY, result.ref_key) or {}
    manual = client.get_by_key(ENTITY, MANUAL_REF) or {}

    report = {
        "author_key": author,
        "deleted_old_msg_ref": OLD_MSG_REF,
        "new_msg_ref": result.ref_key,
        "new_fields": {k: new_record.get(k) for k in COMPARE_FIELDS},
        "manual_fields": {k: manual.get(k) for k in COMPARE_FIELDS},
        "nav_Изменил_new": nav_modified_by(base, auth, result.ref_key),
        "nav_Изменил_manual": nav_modified_by(base, auth, MANUAL_REF),
        "fix_ok": (
            new_record.get("Изменил_Key") == author
            and new_record.get("Редактирует_Key") in (None, "", "00000000-0000-0000-0000-000000000000")
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
