"""Debug OData POST for .msg attachment."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings
from agent_pochta.services.email_msg import eml_bytes_to_msg_bytes, is_msg_bytes
from agent_pochta.services.odata_attached_file import (
    AttachedFileInput,
    build_attached_file_payload,
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient

DOC = "НП00-003877"
REF = "fdb2cd68-8669-11f1-984a-6cb31113810e"
ENTITY_ATTACH = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"


def main() -> None:
    settings = get_settings()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    fm = load_attached_file_field_map()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    flt = f"ВладелецФайла_Key eq guid'{REF}'"
    url = f"{base}{quote(ENTITY_ATTACH)}?$format=json&$filter={quote(flt)}&$orderby=Ref_Key desc&$top=5"
    items = httpx.get(url, auth=auth, timeout=60).json()["value"]
    eml = next(i for i in items if i.get("Description") == "Входящее_письмо")
    eml_bytes = read_attached_file_storage_bytes(
        client, entity=ENTITY_ATTACH, ref_key=eml["Ref_Key"], field_map=fm
    )
    msg_bytes = eml_bytes_to_msg_bytes(eml_bytes)
    print("eml", len(eml_bytes), "msg", len(msg_bytes), "is_msg", is_msg_bytes(msg_bytes))

    processed = datetime.now(ZoneInfo("Europe/Moscow"))
    tests = [
        ("doc_msg_no_author", AttachedFileInput(filename=f"{DOC}.msg", content=msg_bytes, processed_at=processed)),
        ("legacy_eml_small", AttachedFileInput(filename="Входящее_письмо.eml", content=eml_bytes[:5000], processed_at=processed)),
        ("doc_msg_small", AttachedFileInput(filename=f"{DOC}.msg", content=msg_bytes[:5000], processed_at=processed)),
    ]
    for label, inp in tests:
        entity, payload = build_attached_file_payload(
            document_ref_key=REF, file_input=inp, field_map=fm
        )
        payload.pop("Автор_Key", None)
        payload.pop("Редактировал_Key", None)
        try:
            data = client.create_entity(entity, payload)
            rk = data.get("Ref_Key")
            stored = read_attached_file_storage_bytes(
                client, entity=entity, ref_key=rk, field_map=fm
            )
            print(label, "OK", rk, "stored", len(stored))
        except Exception as exc:
            print(label, "FAIL", str(exc)[:400])


if __name__ == "__main__":
    main()
