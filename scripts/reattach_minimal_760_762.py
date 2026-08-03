"""Reattach АЛ00-000760 and АЛ00-000762 with minimal OData POST (406461f template)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.schemas import EmailMessage  # noqa: E402
from agent_pochta.services.email_msg import eml_bytes_to_msg_bytes  # noqa: E402
from agent_pochta.services.erp_attachments import ensure_full_email_bytes_for_erp  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    AttachedFileInput,
    attach_file_to_incoming_document,
    delete_attached_files_for_document,
    load_attached_file_field_map,
    now_attached_file_processed_at,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.odata_integration import resolve_attached_file_author_key  # noqa: E402
from agent_pochta.services.vault import StubVaultClient  # noqa: E402

EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
CASES = (
    ("АЛ00-000760", "20dbfa4d-8689-11f1-984a-6cb31113810e"),
    ("АЛ00-000762", "18516943-871f-11f1-984b-6cb31113810e"),
)
META = (
    "Ref_Key",
    "Description",
    "Размер",
    "ТипХраненияФайла",
    "ФайлХранилище_Type",
    "Автор_Key",
    "Изменил_Key",
    "Редактирует_Key",
    "Том_Key",
    "ПутьКФайлу",
    "DeletionMark",
)


def load_eml(doc_number: str, settings) -> bytes:
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT message_id, mailbox, sender_email, subject, received_at "
                "FROM email_messages WHERE erp_document_number = :doc "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"doc": doc_number},
        ).fetchone()
    if not row:
        raise FileNotFoundError(f"No email row for {doc_number}")
    email = EmailMessage(
        message_id=row.message_id,
        mailbox=row.mailbox or "",
        sender_email=row.sender_email or "",
        subject=row.subject or "",
        received_at=row.received_at,
        attachments=[],
    )
    return ensure_full_email_bytes_for_erp(email, StubVaultClient())


def meta_subset(record: dict) -> dict:
    return {k: record.get(k) for k in META}


def reattach_one(
    client: ODataClient,
    fm: dict,
    *,
    doc_number: str,
    owner_key: str,
    author: str,
    eml_bytes: bytes,
) -> dict:
    deleted = delete_attached_files_for_document(
        client, document_ref_key=owner_key, field_map=fm
    )
    msg_bytes = eml_bytes_to_msg_bytes(eml_bytes, embed_attachments=True)
    result = attach_file_to_incoming_document(
        client,
        document_ref_key=owner_key,
        file_input=AttachedFileInput(
            filename=f"{doc_number}.msg",
            content=msg_bytes,
            author_key=author,
            processed_at=now_attached_file_processed_at(),
        ),
        field_map=fm,
        verify_owner_exists=False,
    )
    entity = fm["entity"]
    stored = read_attached_file_storage_bytes(
        client, entity=entity, ref_key=result.ref_key, field_map=fm
    )
    meta = client.get_by_key(entity, result.ref_key) or {}
    return {
        "doc_number": doc_number,
        "owner_key": owner_key,
        "deleted_refs": deleted,
        "ref_key": result.ref_key,
        "size_local": len(msg_bytes),
        "size_stored": len(stored),
        "stored_eq_local": stored == msg_bytes,
        "metadata": meta_subset(meta),
        "pass": (
            stored == msg_bytes
            and len(stored) > 50_000
            and meta.get("ТипХраненияФайла") == "ВИнформационнойБазе"
            and str(meta.get("Изменил_Key") or EMPTY_GUID) == EMPTY_GUID
            and str(meta.get("Редактирует_Key") or EMPTY_GUID) == EMPTY_GUID
            and meta.get("DeletionMark") is False
        ),
        "verify_in_1c": (
            f"Open {doc_number}, attachment Ref_Key={result.ref_key}, "
            f"Description={meta.get('Description')}"
        ),
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    fm = load_attached_file_field_map()
    author = resolve_attached_file_author_key()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )

    report = {"author_key": author, "docs": []}
    for doc_number, owner_key in CASES:
        eml = load_eml(doc_number, settings)
        report["docs"].append(
            reattach_one(
                client,
                fm,
                doc_number=doc_number,
                owner_key=owner_key,
                author=author,
                eml_bytes=eml,
            )
        )

    out_dir = ROOT / "data" / "temp" / "reattach_minimal"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "reattach_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
