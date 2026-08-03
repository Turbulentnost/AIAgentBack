"""Probe OData POST for .msg attachments — isolate 500 root cause."""
from __future__ import annotations

import base64
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings
from agent_pochta.services.odata_attached_file import (
    AttachedFileInput,
    build_attached_file_payload,
    load_attached_file_field_map,
)
from agent_pochta.services.odata_client import ODataClient
from agent_pochta.services.odata_integration import resolve_attached_file_author_key

DOC_REF = "fdb2cd68-8669-11f1-984a-6cb31113810e"
DOC_NUMBER = "НП00-003877"

# Minimal OLE compound file header (not a valid MSG, but tests size/type paths)
OLE_HEADER = bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 504


def try_post(client: ODataClient, entity: str, payload: dict, label: str) -> dict:
    p = copy.deepcopy(payload)
    b64 = p.get("ФайлХранилище_Base64Data")
    if b64:
        p["ФайлХранилище_Base64Data"] = f"<base64 {len(b64)} chars>"
    try:
        data = client.create_entity(entity, payload)
        ref = data.get("Ref_Key", "")
        return {"label": label, "ok": True, "ref_key": ref}
    except Exception as exc:
        return {"label": label, "ok": False, "error": str(exc)}


def build_payload(
    *,
    description: str,
    extension: str,
    content: bytes,
    content_type: str | None = None,
    author_key: str | None = None,
    field_map: dict | None = None,
) -> tuple[str, dict]:
    fm = copy.deepcopy(field_map or load_attached_file_field_map())
    entity, payload = build_attached_file_payload(
        document_ref_key=DOC_REF,
        file_input=AttachedFileInput(
            filename=f"{description}.{extension}",
            content=content,
            author_key=author_key,
        ),
        field_map=fm,
    )
    if content_type is not None:
        payload["ФайлХранилище_Type"] = content_type
    return entity, payload


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=settings.odata_timeout_sec,
    )
    field_map = load_attached_file_field_map()
    author_key = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key,
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )

    tiny = OLE_HEADER
    big = OLE_HEADER + b"\x00" * (1024 * 1024 - len(OLE_HEADER))

    cases: list[tuple[str, str, dict]] = []

    for desc, ext, content, ctype, auth in [
        ("Входящее_письмо", "eml", tiny, "application/octet-stream", None),
        ("Входящее_письмо", "msg", tiny, "application/octet-stream", None),
        ("Входящее_письмо", "msg", tiny, "application/vnd.ms-outlook", None),
        (DOC_NUMBER, "eml", tiny, "application/octet-stream", None),
        (DOC_NUMBER, "msg", tiny, "application/octet-stream", None),
        (DOC_NUMBER, "msg", tiny, "application/vnd.ms-outlook", None),
        (DOC_NUMBER, "msg", tiny, "application/vnd.ms-outlook", author_key),
        ("НП00003877", "msg", tiny, "application/vnd.ms-outlook", None),
        (DOC_NUMBER, "msg", big, "application/vnd.ms-outlook", None),
        (DOC_NUMBER, "msg", big, "application/octet-stream", None),
    ]:
        entity, payload = build_payload(
            description=desc,
            extension=ext,
            content=content,
            content_type=ctype,
            author_key=auth,
            field_map=field_map,
        )
        cases.append((f"{desc}.{ext} ctype={ctype} size={len(content)} auth={bool(auth)}", entity, payload))

    results = []
    for label, entity, payload in cases:
        results.append(try_post(client, entity, payload, label))

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
