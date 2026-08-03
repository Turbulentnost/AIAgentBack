"""Final diagnostics + reattach for АЛ00-000762 attachment open failure in 1C thick client."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.email_msg import eml_bytes_to_msg_bytes  # noqa: E402
from agent_pochta.services.erp_attachments import ensure_full_email_bytes_for_erp  # noqa: E402
from agent_pochta.schemas import EmailMessage  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    AttachedFileInput,
    attach_file_to_incoming_document,
    delete_attached_files_for_document,
    load_attached_file_field_map,
    now_attached_file_processed_at,
    read_attached_file_storage_bytes,
    release_attached_file_edit_lock,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.odata_integration import resolve_attached_file_author_key  # noqa: E402
from agent_pochta.services.vault import StubVaultClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

DOC_NUMBER = "АЛ00-000762"
DOC762 = "18516943-871f-11f1-984b-6cb31113810e"
DOC760 = "20dbfa4d-8689-11f1-984a-6cb31113810e"
REF_760 = "27997dc5-8689-11f1-984a-6cb31113810e"
REF_LATEST_BROKEN = "996c95b0-8765-11f1-984c-6cb31113810e"
DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
OUT_DIR = ROOT / "data" / "temp" / "final_fix_762"
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"

META_KEYS = (
    "Ref_Key",
    "Description",
    "Расширение",
    "Размер",
    "ТипХраненияФайла",
    "Том_Key",
    "ПутьКФайлу",
    "ФайлХранилище_Type",
    "Автор_Key",
    "Изменил_Key",
    "Редактирует_Key",
    "DeletionMark",
    "ДатаСоздания",
    "ДатаМодификацииУниверсальная",
    "ХранитьВерсии",
    "ПодписанЭП",
    "Зашифрован",
)

DOC_COMPARE_SKIP = {
    "Ref_Key",
    "Number",
    "Date",
    "Posted",
    "DeletionMark",
    "Дата",
    "Номер",
}


def msg_embedded_count(content: bytes) -> int:
    import olefile

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


def first_byte_diff(a: bytes, b: bytes, limit: int = 20) -> list[dict]:
    diffs: list[dict] = []
    max_len = max(len(a), len(b))
    for i in range(max_len):
        if i >= len(a) or i >= len(b) or a[i] != b[i]:
            diffs.append(
                {
                    "offset": i,
                    "a": a[i] if i < len(a) else None,
                    "b": b[i] if i < len(b) else None,
                }
            )
            if len(diffs) >= limit:
                break
    return diffs


def load_762_eml_bytes(settings) -> tuple[bytes, str]:
    for path in sorted((ROOT / "data/temp/compare_762").glob("*000762.eml")):
        if "PROBE" not in path.name:
            return path.read_bytes(), str(path)
    for path in sorted((ROOT / "data/temp/download_762").glob("*.eml")):
        return path.read_bytes(), str(path)

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
        raise FileNotFoundError(f"762 EML not found for {DOC_NUMBER}")
    email = EmailMessage(
        message_id=row.message_id,
        mailbox=row.mailbox or "",
        sender_email=row.sender_email or "",
        subject=row.subject or "",
        received_at=row.received_at,
        attachments=[],
    )
    return ensure_full_email_bytes_for_erp(email, StubVaultClient()), f"imap:{row.message_id}"


def fetch_doc(base: str, auth: tuple[str, str], ref_key: str) -> dict:
    url = f"{base}{quote(DOC_ENTITY)}(guid'{ref_key}')?$format=json"
    return httpx.get(url, auth=auth, timeout=120).json()


def list_attachments(base: str, auth: tuple[str, str], entity: str, owner: str) -> list[dict]:
    flt = f"ВладелецФайла_Key eq guid'{owner}'"
    url = (
        f"{base}{quote(entity)}?$format=json"
        f"&$filter={quote(flt)}&$orderby=ДатаСоздания desc&$top=30"
    )
    return httpx.get(url, auth=auth, timeout=120).json().get("value", [])


def meta_subset(record: dict) -> dict:
    return {k: record.get(k) for k in META_KEYS if k in record}


def upload_probe(
    client: ODataClient,
    fm: dict,
    *,
    label: str,
    filename: str,
    content: bytes,
    author_key: str | None,
    volume_mode: bool = False,
) -> dict:
    probe_fm = copy.deepcopy(fm)
    defaults = dict(probe_fm.get("defaults") or {})
    if volume_mode:
        defaults["storage_mode"] = "volume"
        defaults["storage_kind"] = "ВТомахНаДиске"
        defaults["upload_binary_via_stream"] = True
    else:
        defaults["storage_mode"] = "database"
        defaults["storage_kind"] = "ВИнформационнойБазе"
        defaults["upload_binary_via_stream"] = False
    probe_fm["defaults"] = defaults

    result = attach_file_to_incoming_document(
        client,
        document_ref_key=DOC762,
        file_input=AttachedFileInput(
            filename=filename,
            content=content,
            author_key=author_key,
            processed_at=now_attached_file_processed_at(),
        ),
        field_map=probe_fm,
        verify_owner_exists=False,
    )
    entity = probe_fm["entity"]
    stored = read_attached_file_storage_bytes(
        client, entity=entity, ref_key=result.ref_key, field_map=probe_fm
    )
    meta = client.get_by_key(entity, result.ref_key) or {}
    local_path = OUT_DIR / f"{label}_{filename}"
    local_path.write_bytes(content)
    if stored:
        (OUT_DIR / f"{label}_odata_{filename}").write_bytes(stored)

    ref760 = client.get_by_key(entity, REF_760) or {}
    diff_vs_760 = {
        k: {"760": ref760.get(k), "probe": meta.get(k)}
        for k in META_KEYS
        if ref760.get(k) != meta.get(k)
        and k not in {"Ref_Key", "Description", "Размер", "ДатаСоздания", "ДатаМодификацииУниверсальная", "ПутьКФайлу"}
    }
    return {
        "label": label,
        "ref_key": result.ref_key,
        "filename": filename,
        "size_local": len(content),
        "size_stored": len(stored),
        "stored_eq_local": stored == content,
        "embedded_attachments": msg_embedded_count(content) if filename.endswith(".msg") else None,
        "metadata": meta_subset(meta),
        "metadata_diff_vs_760": diff_vs_760,
        "verify_in_1c": f"Open {DOC_NUMBER}, attachment Ref_Key={result.ref_key} ({label})",
    }


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
    author = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key or "",
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "doc_number": DOC_NUMBER,
        "doc762_ref": DOC762,
        "doc760_ref": DOC760,
        "ref_760_attachment": REF_760,
        "ref_latest_broken": REF_LATEST_BROKEN,
        "code_fix": "omit_Изменил_Key_match_760_template",
    }

    # --- 6. Document-level OData compare ---
    doc760 = fetch_doc(base, auth, DOC760)
    doc762 = fetch_doc(base, auth, DOC762)
    report["document_compare"] = {
        "760": {k: doc760.get(k) for k in sorted(doc760) if not k.startswith("@")},
        "762": {k: doc762.get(k) for k in sorted(doc762) if not k.startswith("@")},
        "field_diff": {
            k: {"760": doc760.get(k), "762": doc762.get(k)}
            for k in sorted(set(doc760) | set(doc762))
            if doc760.get(k) != doc762.get(k) and k not in DOC_COMPARE_SKIP
        },
    }

    # --- 4. Byte compare streams ---
    bytes760 = read_attached_file_storage_bytes(client, entity=entity, ref_key=REF_760, field_map=fm)
    bytes_latest = read_attached_file_storage_bytes(
        client, entity=entity, ref_key=REF_LATEST_BROKEN, field_map=fm
    )
    eml_bytes, eml_source = load_762_eml_bytes(settings)
    msg_local = eml_bytes_to_msg_bytes(eml_bytes, embed_attachments=True)
    local_agent = ROOT / "data/temp/download_762/agent_local_Заявка!.msg"
    local_bytes = local_agent.read_bytes() if local_agent.is_file() else msg_local

    for label, data in (
        ("760_stream", bytes760),
        ("996c95b0_stream", bytes_latest),
        ("agent_local", local_bytes),
    ):
        path = OUT_DIR / f"bytes_{label}.msg"
        if data:
            path.write_bytes(data)

    report["byte_compare"] = {
        "760_vs_latest": {
            "len_760": len(bytes760),
            "len_latest": len(bytes_latest),
            "equal": bytes760 == bytes_latest,
            "sha256_760": hashlib.sha256(bytes760).hexdigest() if bytes760 else None,
            "sha256_latest": hashlib.sha256(bytes_latest).hexdigest() if bytes_latest else None,
            "first_diffs": first_byte_diff(bytes760, bytes_latest),
            "cfb_760": bytes760[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
            "cfb_latest": bytes_latest[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
            "embedded_760": msg_embedded_count(bytes760) if bytes760 else 0,
            "embedded_latest": msg_embedded_count(bytes_latest) if bytes_latest else 0,
        },
        "local_vs_latest": {
            "len_local": len(local_bytes),
            "len_latest": len(bytes_latest),
            "equal": local_bytes == bytes_latest,
            "sha256_local": hashlib.sha256(local_bytes).hexdigest(),
            "first_diffs": first_byte_diff(local_bytes, bytes_latest),
        },
        "760_meta": meta_subset(client.get_by_key(entity, REF_760) or {}),
        "latest_meta": meta_subset(client.get_by_key(entity, REF_LATEST_BROKEN) or {}),
    }

    existing = list_attachments(base, auth, entity, DOC762)
    report["attachments_before_probes"] = [
        {"ref": i.get("Ref_Key"), "desc": i.get("Description"), "meta": meta_subset(i)}
        for i in existing
    ]

    probes: list[dict] = []

    # --- 1. Isolation test v2: exact 760 bytes on 762 ---
    if bytes760:
        probes.append(
            upload_probe(
                client,
                fm,
                label="isolation_v2_760_bytes",
                filename=f"{DOC_NUMBER}.msg",
                content=bytes760,
                author_key=author or None,
                volume_mode=False,
            )
        )

    # --- 5. EML instead of MSG ---
    probes.append(
        upload_probe(
            client,
            fm,
            label="eml_only",
            filename=f"{DOC_NUMBER}.eml",
            content=eml_bytes,
            author_key=author or None,
            volume_mode=False,
        )
    )

    # --- 3. Volume + subject filename (Outlook pattern) ---
    subject = "Заявка!"
    try:
        eml_msg = BytesParser(policy=policy.default).parsebytes(eml_bytes)
        subject = (eml_msg.get("Subject") or subject).strip() or subject
    except Exception:
        pass
    msg_full = eml_bytes_to_msg_bytes(eml_bytes, embed_attachments=True)
    probes.append(
        upload_probe(
            client,
            fm,
            label="volume_outlook_subject",
            filename=f"{subject}.msg",
            content=msg_full,
            author_key=None,
            volume_mode=True,
        )
    )

    report["probes"] = probes
    isolation = next((p for p in probes if p["label"] == "isolation_v2_760_bytes"), None)
    if isolation:
        report["isolation_v2_conclusion"] = (
            "If Ref_Key "
            f"{isolation['ref_key']} (exact 760 bytes on doc 762) still won't open in 1C, "
            "the document itself is likely corrupted — contact 1C admin or recreate АЛ00-000762."
        )

    # --- 2 + 7. Final reattach: delete all, single MSG, doc number, no Изменил_Key ---
    deleted = delete_attached_files_for_document(client, document_ref_key=DOC762, field_map=fm)
    final_msg = eml_bytes_to_msg_bytes(eml_bytes, embed_attachments=True)
    final = attach_file_to_incoming_document(
        client,
        document_ref_key=DOC762,
        file_input=AttachedFileInput(
            filename=f"{DOC_NUMBER}.msg",
            content=final_msg,
            author_key=author or None,
            processed_at=now_attached_file_processed_at(),
        ),
        field_map=fm,
        verify_owner_exists=False,
    )
    stored_final = read_attached_file_storage_bytes(
        client, entity=entity, ref_key=final.ref_key, field_map=fm
    )
    meta_final = client.get_by_key(entity, final.ref_key) or {}
    ref760_meta = client.get_by_key(entity, REF_760) or {}

    (OUT_DIR / f"final_{DOC_NUMBER}.msg").write_bytes(final_msg)
    if stored_final:
        (OUT_DIR / f"final_odata_{DOC_NUMBER}.msg").write_bytes(stored_final)

    report["final_reattach"] = {
        "deleted_refs": deleted,
        "eml_source": eml_source,
        "ref_key": final.ref_key,
        "filename": f"{DOC_NUMBER}.msg",
        "description": meta_final.get("Description"),
        "size_local": len(final_msg),
        "size_stored": len(stored_final),
        "stored_eq_local": stored_final == final_msg,
        "embedded_attachments": msg_embedded_count(final_msg),
        "metadata": meta_subset(meta_final),
        "metadata_diff_vs_760": {
            k: {"760": ref760_meta.get(k), "762": meta_final.get(k)}
            for k in META_KEYS
            if ref760_meta.get(k) != meta_final.get(k)
            and k
            not in {
                "Ref_Key",
                "Description",
                "Размер",
                "ДатаСоздания",
                "ДатаМодификацииУниверсальная",
            }
        },
        "verify_in_1c": (
            f"Open {DOC_NUMBER} dated 24.07.2026, single attachment Ref_Key={final.ref_key}"
        ),
        "pass": (
            len(stored_final) > 50_000
            and stored_final == final_msg
            and meta_final.get("ТипХраненияФайла") == "ВИнформационнойБазе"
            and meta_final.get("DeletionMark") is False
            and str(meta_final.get("Изменил_Key") or EMPTY_GUID) == EMPTY_GUID
            and str(meta_final.get("Редактирует_Key") or EMPTY_GUID) == EMPTY_GUID
        ),
    }

    report_path = OUT_DIR / "final_fix_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
