"""Re-upload НП00-003900 in volume+preupload mode (thick client fix)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ODATA_ATTACH_STAGING_ENABLED", "false")
os.environ.setdefault("ODATA_ATTACH_STAGING_DELETE_AFTER_SUCCESS", "false")
os.environ.setdefault("ODATA_FILE_STORAGE_MODE", "volume")
os.environ.setdefault("ODATA_FILE_VOLUME_PREUPLOAD", "true")

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
    list_attached_files_for_document,
    load_attached_file_field_map,
    now_attached_file_processed_at,
    read_attached_file_storage_bytes,
    replace_attached_files_for_document,
    resolve_volume_root,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.odata_integration import (  # noqa: E402
    ODataIntegrationService,
    resolve_attached_file_author_key,
)
from agent_pochta.services.vault import StubVaultClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

DOC_NUMBER = "НП00-003900"
REF_OLD_VOLUME = "0689f586-39f5-11f0-9679-6cb31113810c"
REF_CURRENT_DB = "e351f21d-89af-11f1-984f-6cb31113810c"
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
    "СтатусИзвлеченияТекста",
)
SKIP_DIFF = {"Ref_Key", "Description", "Размер", "ДатаСоздания", "ДатаМодификацииУниверсальная", "ПутьКФайлу"}


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
        file_storage_mode=settings.odata_file_storage_mode,
        file_volume_root=settings.odata_file_volume_root,
        file_volume_preupload=settings.odata_file_volume_preupload,
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

    ref_old = client.get_by_key(entity, REF_OLD_VOLUME) or {}
    ref_current = client.get_by_key(entity, REF_CURRENT_DB) or {}
    volume_root = resolve_volume_root(client, defaults=fm.get("defaults") or {})
    existing_before = list_attached_files_for_document(
        client, document_ref_key=doc_ref, field_map=fm
    )

    replace_result = replace_attached_files_for_document(
        client,
        document_ref_key=doc_ref,
        files=[
            AttachedFileInput(
                filename=msg_filename,
                content=msg_bytes,
                author_key=author or None,
                processed_at=now_attached_file_processed_at(),
            )
        ],
        field_map=fm,
        verify_owner_exists=True,
        document_number=DOC_NUMBER,
        message_id=email.message_id,
    )
    result = replace_result.attached[0]

    odata_bytes = read_attached_file_storage_bytes(
        client, entity=entity, ref_key=result.ref_key, field_map=fm
    )
    meta = client.get_by_key(entity, result.ref_key) or {}

    report = {
        "fixed_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy": "volume+preupload (transactional replace)",
        "volume_root": volume_root,
        "document_number": DOC_NUMBER,
        "document_ref_key": doc_ref,
        "existing_before_count": len(existing_before),
        "deleted_old_refs": list(replace_result.deleted_old_refs),
        "new_ref_key": result.ref_key,
        "msg_size": len(msg_bytes),
        "odata_stream_size": len(odata_bytes),
        "new_meta": {k: meta.get(k) for k in META_KEYS},
        "old_volume_meta": {k: ref_old.get(k) for k in META_KEYS},
        "previous_db_meta": {k: ref_current.get(k) for k in META_KEYS},
        "meta_diff_vs_old_volume": {
            k: {"old_volume": ref_old.get(k), "new": meta.get(k)}
            for k in META_KEYS
            if ref_old.get(k) != meta.get(k) and k not in SKIP_DIFF
        },
        "editor_key_empty": str(meta.get("Редактирует_Key") or EMPTY_GUID) == EMPTY_GUID,
        "roundtrip_ok": result.roundtrip_ok,
        "staging_path": result.staging_path,
    }

    out = ROOT / "data" / "temp" / "fix_003900_volume_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
