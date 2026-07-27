"""Restore/re-upload НП00-003900 attachment in database mode (safe fallback)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ODATA_ATTACH_STAGING_ENABLED", "false")
os.environ.setdefault("ODATA_ATTACH_STAGING_DELETE_AFTER_SUCCESS", "false")
os.environ.setdefault("ODATA_FILE_STORAGE_MODE", "database")
os.environ.setdefault("ODATA_FILE_VOLUME_PREUPLOAD", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.db.models import EmailMessageRow  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.schemas import EmailMessage  # noqa: E402
from agent_pochta.services.email_msg import eml_bytes_to_msg_bytes  # noqa: E402
from agent_pochta.services.erp_attachments import (  # noqa: E402
    ensure_full_email_bytes_for_erp,
    erp_full_email_filename,
)
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    AttachedFileInput,
    attach_file_to_incoming_document,
    list_attached_files_for_document,
    now_attached_file_processed_at,
    read_attached_file_storage_bytes,
    replace_attached_files_for_document,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.odata_integration import (  # noqa: E402
    ODataIntegrationService,
    resolve_attached_file_author_key,
)
from agent_pochta.services.vault import StubVaultClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

DOC_NUMBER = "НП00-003900"
REF_760_OK = "b63a9c9d-8767-11f1-984c-6cb31113810e"
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
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
    "Description",
    "Расширение",
)
SKIP_DIFF = {"Ref_Key", "Description", "Размер", "ДатаСоздания", "ДатаМодификацииУниверсальная"}


def load_email_row() -> EmailMessageRow:
    factory = get_session_factory()
    with factory() as session:
        row = session.scalar(
            select(EmailMessageRow)
            .where(EmailMessageRow.erp_document_number == DOC_NUMBER)
            .order_by(EmailMessageRow.id.desc())
        )
        if row is None:
            raise SystemExit(f"No DB row for {DOC_NUMBER}")
        session.expunge(row)
        return row


def build_field_map(settings) -> dict:
    svc = ODataIntegrationService(
        settings.odata_base_url,
        entity=settings.odata_incoming_doc_entity,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=max(settings.odata_timeout_sec, 120),
        file_volume_key=settings.odata_file_volume_key,
        file_author_key=settings.odata_file_author_key,
        file_storage_mode="database",
        file_volume_root=settings.odata_file_volume_root,
        file_volume_preupload=False,
    )
    return svc._attached_file_field_map


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    row = load_email_row()
    doc_ref = (row.erp_task_id or "").strip()
    email = EmailMessage(
        message_id=row.message_id,
        mailbox=row.mailbox or "",
        sender_email=row.sender_email or "",
        subject=row.subject or "",
        received_at=row.received_at,
        attachments=[],
    )
    eml_bytes = ensure_full_email_bytes_for_erp(email, StubVaultClient())
    msg_filename = erp_full_email_filename(email, erp_document_number=DOC_NUMBER)
    msg_bytes = eml_bytes_to_msg_bytes(eml_bytes, embed_attachments=True)

    settings = get_settings()
    fm = build_field_map(settings)
    entity = fm["entity"]
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=max(settings.odata_timeout_sec, 120),
    )
    author = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key or "",
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )

    ref760 = client.get_by_key(entity, REF_760_OK) or {}
    existing_before = list_attached_files_for_document(
        client, document_ref_key=doc_ref, field_map=fm
    )
    file_input = AttachedFileInput(
        filename=msg_filename,
        content=msg_bytes,
        author_key=author or None,
        processed_at=now_attached_file_processed_at(),
    )

    if existing_before:
        replace_result = replace_attached_files_for_document(
            client,
            document_ref_key=doc_ref,
            files=[file_input],
            field_map=fm,
            verify_owner_exists=True,
            document_number=DOC_NUMBER,
            message_id=email.message_id,
        )
        result = replace_result.attached[0]
        deleted = list(replace_result.deleted_old_refs)
        strategy = "database (transactional replace)"
    else:
        result = attach_file_to_incoming_document(
            client,
            document_ref_key=doc_ref,
            file_input=file_input,
            field_map=fm,
            verify_owner_exists=True,
            document_number=DOC_NUMBER,
            message_id=email.message_id,
        )
        deleted = []
        strategy = "database (attach only, doc was empty)"

    odata_bytes = read_attached_file_storage_bytes(
        client, entity=entity, ref_key=result.ref_key, field_map=fm
    )
    meta = client.get_by_key(entity, result.ref_key) or {}

    report = {
        "fixed_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy,
        "document_number": DOC_NUMBER,
        "document_ref_key": doc_ref,
        "existing_before_count": len(existing_before),
        "deleted_old_refs": deleted,
        "new_ref_key": result.ref_key,
        "bytes_match": odata_bytes == msg_bytes,
        "msg_size": len(msg_bytes),
        "odata_size": len(odata_bytes),
        "new_meta": {k: meta.get(k) for k in META_KEYS},
        "760_meta": {k: ref760.get(k) for k in META_KEYS},
        "meta_diff_vs_760": {
            k: {"760": ref760.get(k), "3900": meta.get(k)}
            for k in META_KEYS
            if ref760.get(k) != meta.get(k) and k not in SKIP_DIFF
        },
        "editor_key_empty": str(meta.get("Редактирует_Key") or EMPTY_GUID) == EMPTY_GUID,
        "roundtrip_ok": result.roundtrip_ok,
        "staging_path": result.staging_path,
    }

    out = ROOT / "data" / "temp" / "fix_003900_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
