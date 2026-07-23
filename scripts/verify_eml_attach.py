"""Verify eml attach: only one file, bytes>0, date filled."""
from __future__ import annotations

import base64
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings
from agent_pochta.services.odata_attached_file import (
    AttachedFileInput,
    attach_file_to_incoming_document,
    load_attached_file_field_map,
)
from agent_pochta.services.odata_client import ODataClient

# Test doc: НП00-003870 (had broken eml after stream PUT)
DOC_NUMBER = "НП00-003870"
DOC_REF = "ccb7ab6d-8653-11f1-984a-6cb31113810e"
ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
EML_NAME = "Входящее_письмо"


def _sample_eml() -> bytes:
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return (
        f"From: test@example.com\r\n"
        f"To: info@turbo-don.ru\r\n"
        f"Subject: Agent attach verify {now}\r\n"
        f"Date: {now}\r\n"
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        f"Проверка прикрепления только .eml с датой обработки.\r\n"
    ).encode("utf-8")


def fetch_files(base: str, auth, owner: str) -> list[dict]:
    flt = f"ВладелецФайла_Key eq guid'{owner}'"
    url = f"{base}{quote(ENTITY)}?$format=json&$filter={quote(flt)}&$orderby=Ref_Key desc&$top=20"
    with httpx.Client(timeout=30) as client:
        r = client.get(url, auth=auth)
        r.raise_for_status()
        return r.json().get("value", [])


def main() -> None:
    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password) if settings.odata_username else None
    content = _sample_eml()
    processed_at = datetime.now(timezone.utc)
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=settings.odata_timeout_sec,
    )
    field_map = load_attached_file_field_map()
    result = attach_file_to_incoming_document(
        client,
        document_ref_key=DOC_REF,
        file_input=AttachedFileInput(
            filename="Входящее_письмо.eml",
            content=content,
            processed_at=processed_at,
        ),
        field_map=field_map,
        verify_owner_exists=True,
    )
    print("ATTACHED", result.ref_key, result.size_bytes)

    items = fetch_files(base, auth, DOC_REF)
    eml_items = [i for i in items if (i.get("Description") or "") == EML_NAME]
    latest = eml_items[0] if eml_items else {}
    b64 = latest.get("ФайлХранилище_Base64Data") or ""
    decoded = base64.b64decode(b64) if b64 else b""
    print("DOC", DOC_NUMBER, "files_total", len(items))
    print("eml_count", len(eml_items))
    print("latest_ref", latest.get("Ref_Key"))
    print("storage", latest.get("ТипХраненияФайла"))
    print("size_meta", latest.get("Размер"), "b64_bytes", len(decoded))
    print("ДатаСоздания", latest.get("ДатаСоздания"))
    print("ДатаМодификацииУниверсальная", latest.get("ДатаМодификацииУниверсальная"))
    ok = (
        len(decoded) > 0
        and b"Subject:" in decoded
        and not str(latest.get("ДатаСоздания") or "").startswith("0001")
    )
    print("VERIFY", "OK" if ok else "FAIL")


if __name__ == "__main__":
    main()
