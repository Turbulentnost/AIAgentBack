"""Delete old attachments and re-upload АЛ00-000762 (volume mode, manual Outlook pattern)."""
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
from agent_pochta.services.email_msg import (  # noqa: E402
    eml_bytes_to_msg_bytes,
    normalize_attachment_filename,
)
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    AttachedFileInput,
    attach_files_to_incoming_document,
    build_volume_storage_filename,
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
DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
EML_FALLBACK = ROOT / "data/temp/compare_762/АЛ00-000762_2026-07-24T07-17-23_АЛ00-000762.eml"
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
)


def volume_field_map(base: dict) -> dict:
    defaults = dict(base.get("defaults") or {})
    defaults["storage_mode"] = "volume"
    defaults["storage_kind"] = "ВТомахНаДиске"
    return {**base, "defaults": defaults}


def newest_doc_ref(base: str, auth, number: str, doc_entity: str) -> str | None:
    url = (
        f"{base}{quote(doc_entity)}?$format=json"
        f"&$filter={quote(f'Number eq {chr(39)}{number}{chr(39)}')}"
        f"&$orderby=Date desc&$top=1"
    )
    items = httpx.get(url, auth=auth, timeout=120).json().get("value", [])
    return items[0].get("Ref_Key") if items else None


def extract_eml_subject(eml_bytes: bytes) -> str:
    msg = BytesParser(policy=policy.default).parsebytes(eml_bytes)
    return (msg.get("Subject") or "").strip()


def load_762_eml_bytes(settings) -> tuple[bytes, str]:
    """Локальный EML или IMAP по строке email_messages для АЛ00-000762."""
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


def extract_pdf_attachment(eml_bytes: bytes) -> tuple[str, bytes] | None:
    msg = BytesParser(policy=policy.default).parsebytes(eml_bytes)
    for part in msg.walk():
        fn = part.get_filename()
        if not fn:
            continue
        payload = part.get_payload(decode=True) or b""
        if not payload:
            continue
        name = normalize_attachment_filename(fn) or fn
        return name, payload
    return None


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
    fm = volume_field_map(load_attached_file_field_map())
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    doc_ref = newest_doc_ref(base, auth, DOC_NUMBER, settings.odata_incoming_doc_entity)
    if not doc_ref:
        print(json.dumps({"error": f"document {DOC_NUMBER} not found"}, ensure_ascii=False))
        raise SystemExit(1)

    eml_bytes, eml_source = load_762_eml_bytes(settings)
    subject = extract_eml_subject(eml_bytes) or DOC_NUMBER
    msg_filename = build_volume_storage_filename(subject, "msg")

    deleted = delete_attached_files_for_document(
        client, document_ref_key=doc_ref, field_map=fm
    )
    msg_bytes = eml_bytes_to_msg_bytes(eml_bytes, embed_attachments=False)
    pdf = extract_pdf_attachment(eml_bytes)
    if not pdf:
        print(json.dumps({"error": "no attachment in EML"}, ensure_ascii=False))
        raise SystemExit(1)
    pdf_name, pdf_bytes = pdf

    author = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key or "",
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )
    attach_time = now_attached_file_processed_at()
    results = attach_files_to_incoming_document(
        client,
        document_ref_key=doc_ref,
        files=[
            AttachedFileInput(
                filename=msg_filename,
                content=msg_bytes,
                author_key=author or None,
                processed_at=attach_time,
            ),
            AttachedFileInput(
                filename=pdf_name,
                content=pdf_bytes,
                author_key=author or None,
                processed_at=attach_time,
            ),
        ],
        field_map=fm,
        verify_owner_exists=False,
    )
    msg_result = results[0]
    pdf_result = results[1]
    stored_msg = read_attached_file_storage_bytes(
        client, entity=msg_result.entity, ref_key=msg_result.ref_key, field_map=fm
    )
    stored_pdf = read_attached_file_storage_bytes(
        client, entity=pdf_result.entity, ref_key=pdf_result.ref_key, field_map=fm
    )
    msg_meta = client.get_by_key(msg_result.entity, msg_result.ref_key) or {}
    pdf_meta = client.get_by_key(pdf_result.entity, pdf_result.ref_key) or {}
    report = {
        "document": DOC_NUMBER,
        "doc_ref": doc_ref,
        "eml_source": eml_source,
        "email_subject": subject,
        "msg_filename": msg_filename,
        "deleted_refs": deleted,
        "strategy": "volume-body-only-msg-plus-separate-pdf",
        "storage_mode": "volume",
        "msg_ref_key": msg_result.ref_key,
        "pdf_ref_key": pdf_result.ref_key,
        "pdf_filename": pdf_name,
        "msg_embedded_attachments": msg_embedded_count(msg_bytes),
        "msg_size": len(msg_bytes),
        "pdf_size": len(pdf_bytes),
        "stored_msg_eq_local": stored_msg == msg_bytes,
        "stored_pdf_eq_local": stored_pdf == pdf_bytes,
        "msg_stream_len": len(stored_msg),
        "pdf_stream_len": len(stored_pdf),
        "odata_meta_msg": {k: msg_meta.get(k) for k in META_KEYS},
        "odata_meta_pdf": {k: pdf_meta.get(k) for k in META_KEYS},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
