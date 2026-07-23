"""Verify eml attach: doc number name, author, MSK date, bytes>0."""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings
from agent_pochta.services.odata_attached_file import (
    AttachedFileInput,
    attach_file_to_incoming_document,
    load_attached_file_field_map,
    now_attached_file_processed_at,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient
from agent_pochta.services.odata_integration import resolve_attached_file_author_key

DOC_NUMBER = "НП00-003876"
DOC_REF = "e9e1b18c-8669-11f1-984a-6cb31113810e"
_MSK = ZoneInfo("Europe/Moscow")


def _sample_eml() -> bytes:
    now = datetime.now(_MSK).strftime("%a, %d %b %Y %H:%M:%S %z")
    return (
        f"From: test@example.com\r\n"
        f"To: info@turbo-don.ru\r\n"
        f"Subject: Agent attach verify {now}\r\n"
        f"Date: {now}\r\n"
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        f"Проверка прикрепления .eml с номером документа и автором ИИ.\r\n"
    ).encode("utf-8")


def fetch_files(base: str, auth, owner: str, entity: str) -> list[dict]:
    flt = f"ВладелецФайла_Key eq guid'{owner}'"
    url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$orderby=Ref_Key desc&$top=20"
    with httpx.Client(timeout=30) as client:
        r = client.get(url, auth=auth)
        r.raise_for_status()
        return r.json().get("value", [])


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password) if settings.odata_username else None
    content = _sample_eml()
    processed_at = now_attached_file_processed_at()
    author_key = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key,
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=settings.odata_timeout_sec,
    )
    field_map = load_attached_file_field_map()
    entity = field_map["entity"]
    result = attach_file_to_incoming_document(
        client,
        document_ref_key=DOC_REF,
        file_input=AttachedFileInput(
            filename=f"{DOC_NUMBER}.eml",
            content=content,
            processed_at=processed_at,
            author_key=author_key or None,
            edited_by_key=author_key or None,
        ),
        field_map=field_map,
        verify_owner_exists=True,
    )
    print("ATTACHED", result.ref_key, result.size_bytes, result.filename)

    stored = read_attached_file_storage_bytes(
        client,
        entity=entity,
        ref_key=result.ref_key,
        field_map=field_map,
    )
    print("stored_bytes", len(stored))

    items = fetch_files(base, auth, DOC_REF, entity)
    eml_items = [i for i in items if (i.get("Description") or "") == DOC_NUMBER]
    latest = eml_items[0] if eml_items else {}
    b64 = latest.get("ФайлХранилище_Base64Data") or ""
    decoded = base64.b64decode(b64) if b64 else b""
    print("DOC", DOC_NUMBER, "files_total", len(items))
    print("eml_count", len(eml_items))
    print("latest_ref", latest.get("Ref_Key"))
    print("Description", latest.get("Description"))
    print("Расширение", latest.get("Расширение"))
    print("Автор_Key", latest.get("Автор_Key"))
    print("Редактировал_Key", latest.get("Редактировал_Key"))
    print("storage", latest.get("ТипХраненияФайла"))
    print("size_meta", latest.get("Размер"), "b64_bytes", len(decoded))
    print("ДатаСоздания", latest.get("ДатаСоздания"))
    print("expected_msk", processed_at.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S"))
    ok = (
        len(stored) == len(content)
        and len(decoded) > 0
        and b"Subject:" in decoded
        and latest.get("Description") == DOC_NUMBER
        and latest.get("Расширение") == "eml"
        and author_key
        and latest.get("Автор_Key") == author_key
        and not str(latest.get("ДатаСоздания") or "").startswith("0001")
    )
    print("VERIFY", "OK" if ok else "FAIL")
    print(json.dumps({"author_key": author_key, "ref_key": result.ref_key}, ensure_ascii=False))


if __name__ == "__main__":
    main()
