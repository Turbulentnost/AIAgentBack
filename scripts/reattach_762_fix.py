"""Delete broken volume attachments and re-upload АЛ00-000762 (database + full MSG, 760 template)."""
from __future__ import annotations

import json
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.email_msg import eml_bytes_to_msg_bytes  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    AttachedFileInput,
    attach_file_to_incoming_document,
    delete_attached_files_for_document,
    load_attached_file_field_map,
    now_attached_file_processed_at,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.erp_attachments import ensure_full_email_bytes_for_erp  # noqa: E402
from agent_pochta.schemas import EmailMessage  # noqa: E402
from agent_pochta.services.vault import StubVaultClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.odata_integration import resolve_attached_file_author_key  # noqa: E402

DOC_NUMBER = "АЛ00-000762"
DOC_REF = "18516943-871f-11f1-984b-6cb31113810e"
REF_760 = "27997dc5-8689-11f1-984a-6cb31113810e"
EML_FALLBACK = ROOT / "data/temp/compare_762/АЛ00-000762_2026-07-24T07-17-23_АЛ00-000762.eml"
OUT_DIR = ROOT / "data" / "temp" / "reattach_762"
META_KEYS = (
    "ТипХраненияФайла",
    "Том_Key",
    "ПутьКФайлу",
    "ФайлХранилище_Type",
    "Размер",
    "Редактирует_Key",
    "Изменил_Key",
    "Автор_Key",
    "DeletionMark",
    "ДатаСоздания",
    "Description",
    "Расширение",
)
SKIP_DIFF = {
    "Ref_Key",
    "Description",
    "Размер",
    "ДатаСоздания",
    "ДатаМодификацииУниверсальная",
}


def load_762_eml_bytes(settings) -> tuple[bytes, str]:
    eml_path = EML_FALLBACK
    if not eml_path.is_file():
        eml_path = next(
            (p for p in (ROOT / "data/temp/compare_762").glob("*000762.eml") if "PROBE" not in p.name),
            None,
        )
    if eml_path is not None and eml_path.is_file():
        return eml_path.read_bytes(), str(eml_path)

    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT message_id, mailbox, sender_email, subject, received_at "
                "FROM email_messages WHERE erp_document_number = :doc "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"doc": DOC_NUMBER},
        ).fetchone()
    if not row:
        raise FileNotFoundError(f"762 EML not found locally and no DB row for {DOC_NUMBER}")

    email = EmailMessage(
        message_id=row.message_id,
        mailbox=row.mailbox or "",
        sender_email=row.sender_email or "",
        subject=row.subject or "",
        received_at=row.received_at,
        attachments=[],
    )
    vault = StubVaultClient()
    eml_bytes = ensure_full_email_bytes_for_erp(email, vault)
    return eml_bytes, f"imap:{row.message_id}"


def msg_embedded_count(content: bytes) -> int:
    import olefile
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as tmp:
        path = tmp.name
    try:
        Path(path).write_bytes(content)
        ole = olefile.OleFileIO(path)
        index = 0
        while ole.exists(("__attach_version1.0_#%08X" % index, "__properties_version1.0")):
            index += 1
        ole.close()
        return index
    finally:
        os.unlink(path)


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
        timeout_sec=120,
    )

    eml_bytes, eml_source = load_762_eml_bytes(settings)
    msg_bytes = eml_bytes_to_msg_bytes(eml_bytes, embed_attachments=True)
    msg_filename = f"{DOC_NUMBER}.msg"

    deleted = delete_attached_files_for_document(
        client, document_ref_key=DOC_REF, field_map=fm
    )

    author = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key or "",
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )
    result = attach_file_to_incoming_document(
        client,
        document_ref_key=DOC_REF,
        file_input=AttachedFileInput(
            filename=msg_filename,
            content=msg_bytes,
            author_key=author or None,
            processed_at=now_attached_file_processed_at(),
        ),
        field_map=fm,
        verify_owner_exists=False,
    )

    stored = read_attached_file_storage_bytes(
        client, entity=result.entity, ref_key=result.ref_key, field_map=fm
    )
    meta = client.get_by_key(entity, result.ref_key) or {}
    ref760 = httpx.get(
        f"{base}{quote(entity)}(guid'{REF_760}')?$format=json",
        auth=auth,
        timeout=120,
    ).json()
    diff = {
        k: {"760": ref760.get(k), "762": meta.get(k)}
        for k in META_KEYS
        if ref760.get(k) != meta.get(k) and k not in SKIP_DIFF
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    local_path = OUT_DIR / msg_filename
    local_path.write_bytes(msg_bytes)
    if stored:
        (OUT_DIR / f"odata_{msg_filename}").write_bytes(stored)

    report = {
        "document": DOC_NUMBER,
        "doc_ref": DOC_REF,
        "eml_source": eml_source,
        "deleted_refs": deleted,
        "strategy": "database-full-msg-760-template",
        "storage_mode": "database",
        "msg_ref_key": result.ref_key,
        "msg_filename": msg_filename,
        "msg_embedded_attachments": msg_embedded_count(msg_bytes),
        "msg_size_local": len(msg_bytes),
        "msg_size_stored": len(stored),
        "stored_eq_local": stored == msg_bytes,
        "cfb_magic": stored[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
        "odata_meta": {k: meta.get(k) for k in META_KEYS},
        "metadata_diff_vs_760": diff,
        "verify_in_1c": (
            f"Open {DOC_NUMBER} dated 24.07.2026, attachment Ref_Key={result.ref_key}"
        ),
        "pass": (
            len(stored) > 50_000
            and stored == msg_bytes
            and meta.get("ТипХраненияФайла") == "ВИнформационнойБазе"
            and meta.get("DeletionMark") is False
            and (meta.get("Редактирует_Key") or "00000000-0000-0000-0000-000000000000")
            == "00000000-0000-0000-0000-000000000000"
        ),
    }
    report_path = OUT_DIR / "reattach_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
