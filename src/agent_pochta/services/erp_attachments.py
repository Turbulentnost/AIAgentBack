"""Прикрепление вложений письма к документу 1С после create_incoming_correspondence."""

from __future__ import annotations

import json
from datetime import datetime

import structlog

from agent_pochta.attachments.cache import (
    attachment_cache_key,
    full_email_cache_key,
    get_cached_attachment,
    put_cached_attachment,
)
from agent_pochta.attachments.imap_fetch import ensure_attachments_from_imap
from agent_pochta.config import get_settings
from agent_pochta.imap.client import ImapMailboxClient, resolve_imap_credentials
from agent_pochta.schemas import Attachment, EmailMessage
from agent_pochta.services.integration_service import IntegrationService
from agent_pochta.services.email_msg import eml_bytes_to_msg_bytes
from agent_pochta.services.odata_attached_file import AttachedFileInput, now_attached_file_processed_at
from agent_pochta.services.odata_integration import ODataIntegrationService
from agent_pochta.services.vault import VaultClient

logger = structlog.get_logger(__name__)

_SKIP_ERP_DOCUMENT_NUMBERS = frozenset({"SKIP-ERP", "DRY-RUN"})
ERP_LEGACY_EMAIL_FILENAME = "Входящее_письмо.eml"
# Обратная совместимость тестов и retry API
ERP_FULL_EMAIL_FILENAME = ERP_LEGACY_EMAIL_FILENAME
ERP_LEGACY_EML_BASENAME = "Входящее_письмо"


def existing_erp_document_ref_key(row) -> str | None:
    """Ref_Key документа 1С из БД (erp_task_id хранит erp_document_id после create)."""
    ref = (getattr(row, "erp_task_id", None) or "").strip()
    if not ref or ref in _SKIP_ERP_DOCUMENT_NUMBERS:
        return None
    return ref


def row_has_attachment_metadata(row, email: EmailMessage) -> bool:
    """True, если у письма есть метаданные вложений (байты могут отсутствовать в БД)."""
    if email.attachments:
        return True
    return int(getattr(row, "attachments_count", 0) or 0) > 0


def erp_attachments_already_uploaded(raw_payload_json: str | None) -> bool:
    if not raw_payload_json:
        return False
    try:
        payload = json.loads(raw_payload_json)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    uploaded = payload.get("erp_attachments")
    return isinstance(uploaded, list) and len(uploaded) > 0


def uploaded_erp_attachment_filenames(raw_payload_json: str | None) -> set[str]:
    """Имена файлов, уже отправленных в 1С (для идемпотентной догрузки)."""
    if not raw_payload_json:
        return set()
    try:
        payload = json.loads(raw_payload_json)
    except json.JSONDecodeError:
        return set()
    if not isinstance(payload, dict):
        return set()
    uploaded = payload.get("erp_attachments")
    if not isinstance(uploaded, list):
        return set()
    names: set[str] = set()
    for item in uploaded:
        if isinstance(item, dict):
            name = (item.get("filename") or "").strip()
            ext = (item.get("extension") or "").strip().lstrip(".")
            if name:
                names.add(name)
            if name and ext and "." not in name:
                names.add(f"{name}.{ext}")
    return names


def merge_erp_attachment_lists(existing: list | None, new_items: list) -> list:
    """Объединяет списки erp_attachments без дублей по filename."""
    by_name: dict[str, dict] = {}
    for item in existing or []:
        if isinstance(item, dict):
            name = (item.get("filename") or "").strip()
            if name:
                by_name[name] = item
    for item in new_items:
        if isinstance(item, dict):
            name = (item.get("filename") or "").strip()
            if name:
                by_name[name] = item
    return list(by_name.values())


def clear_erp_attachment_entries(
    raw_payload_json: str | None,
    *,
    filenames: set[str],
) -> str | None:
    """Удаляет записи erp_attachments по имени (перед принудительной перезагрузкой)."""
    if not raw_payload_json or not filenames:
        return raw_payload_json
    try:
        payload = json.loads(raw_payload_json)
    except json.JSONDecodeError:
        return raw_payload_json
    if not isinstance(payload, dict):
        return raw_payload_json
    uploaded = payload.get("erp_attachments")
    if not isinstance(uploaded, list):
        return raw_payload_json
    targets = {name.strip() for name in filenames if name and name.strip()}
    if not targets:
        return raw_payload_json
    filtered = [
        item
        for item in uploaded
        if not (
            isinstance(item, dict)
            and (item.get("filename") or "").strip() in targets
        )
    ]
    if len(filtered) == len(uploaded):
        return raw_payload_json
    payload["erp_attachments"] = filtered
    return json.dumps(payload, ensure_ascii=False)


def resolve_skip_filenames_for_erp_sync(
    raw_payload_json: str | None,
    *,
    force_reattach_filenames: set[str] | None = None,
) -> set[str]:
    """Имена файлов, уже отправленных в 1С; force_reattach исключает их из skip."""
    skip = uploaded_erp_attachment_filenames(raw_payload_json)
    if force_reattach_filenames:
        skip -= {name.strip() for name in force_reattach_filenames if name and name.strip()}
    return skip


def _coerce_attachments(email: EmailMessage) -> None:
    normalized: list[Attachment] = []
    for att in email.attachments or []:
        if isinstance(att, Attachment):
            normalized.append(att)
        elif isinstance(att, dict):
            normalized.append(Attachment.model_validate(att))
    email.attachments = normalized


def attachments_with_content(email: EmailMessage) -> list[Attachment]:
    _coerce_attachments(email)
    return [
        att
        for att in (email.attachments or [])
        if att.content and len(att.content) > 0 and (att.filename or "").strip()
    ]


_MIME_EXTENSION = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/plain": "txt",
    "application/zip": "zip",
}


def erp_attachment_filename(att: Attachment) -> str:
    """Имя файла для OData: расширение обязательно (ТЗ БСП / split_filename)."""
    name = (att.filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not name or name in {".", ".."}:
        name = "attachment"
    if "." not in name:
        ext = _MIME_EXTENSION.get((att.mime_type or "").lower().split(";", 1)[0].strip())
        if ext:
            name = f"{name}.{ext}"
        else:
            name = f"{name}.bin"
    return name


def cache_email_attachment_bytes(email: EmailMessage) -> int:
    """Кладёт байты вложений в in-memory кэш (retry ERP / повторная обработка)."""
    if not email.mailbox:
        return 0
    imap_id = email.message_id.split("#", 1)[0]
    cached = 0
    for index, att in enumerate(email.attachments or []):
        if not att.content or not (att.filename or "").strip():
            continue
        put_cached_attachment(
            attachment_cache_key(email.mailbox, imap_id, index, att.filename),
            content=bytes(att.content),
            mime_type=att.mime_type or "application/octet-stream",
            filename=att.filename,
        )
        cached += 1
    return cached


def _restore_attachments_from_cache(email: EmailMessage) -> int:
    if not email.mailbox:
        return 0
    imap_id = email.message_id.split("#", 1)[0]
    restored = 0
    for index, att in enumerate(email.attachments):
        if att.content or not att.filename or att.size_bytes <= 0:
            continue
        cached = get_cached_attachment(
            attachment_cache_key(email.mailbox, imap_id, index, att.filename)
        )
        if cached and cached.content:
            att.content = cached.content
            if cached.mime_type:
                att.mime_type = cached.mime_type
            restored += 1
    return restored


def _restore_attachments_via_partial_imap(email: EmailMessage, vault: VaultClient) -> int:
    missing = [
        (index, att)
        for index, att in enumerate(email.attachments)
        if att.content is None and att.size_bytes > 0 and (att.filename or "").strip()
    ]
    if not missing or not email.mailbox:
        return 0

    imap_id = email.message_id.split("#", 1)[0]
    settings = get_settings()
    try:
        credentials = resolve_imap_credentials(email.mailbox, vault)
        client = ImapMailboxClient(email.mailbox, credentials, settings=settings)
    except Exception as exc:
        logger.warning(
            "erp_attachment_imap_client_failed",
            message_id=email.message_id,
            mailbox=email.mailbox,
            error=str(exc),
        )
        return 0

    restored = 0
    for index, att in missing:
        if att.content is not None:
            continue
        try:
            fetched = client.fetch_attachment_bytes(
                imap_id,
                filename=att.filename,
                attachment_index=index,
                timeout_sec=settings.imap_download_timeout_sec,
            )
        except Exception as exc:
            logger.warning(
                "erp_attachment_partial_fetch_failed",
                message_id=email.message_id,
                filename=att.filename,
                error=str(exc),
            )
            continue
        if not fetched:
            continue
        content, mime_type, resolved_name = fetched
        if not content:
            continue
        att.content = content
        if mime_type:
            att.mime_type = mime_type
        if resolved_name:
            att.filename = resolved_name
        restored += 1
        logger.info(
            "erp_attachment_partial_fetch_restored",
            message_id=email.message_id,
            filename=att.filename,
            size_bytes=len(content),
        )
    return restored


def ensure_attachment_bytes_for_erp(email: EmailMessage, vault: VaultClient) -> int:
    """Подгружает байты вложений перед отправкой в 1С (кэш → IMAP RFC822 → IMAP partial)."""
    restored = _restore_attachments_from_cache(email)
    if attachments_with_content(email):
        return restored

    restored += ensure_attachments_from_imap(email, vault, load_oversized=True)
    if attachments_with_content(email):
        return restored

    restored += _restore_attachments_via_partial_imap(email, vault)
    still_missing = [
        att.filename
        for att in (email.attachments or [])
        if att.content is None and att.size_bytes > 0
    ]
    if still_missing:
        logger.warning(
            "erp_attachment_bytes_still_missing",
            message_id=email.message_id,
            filenames=still_missing,
        )
    else:
        cache_email_attachment_bytes(email)
    return restored


def erp_full_email_filename(
    _email: EmailMessage | None = None,
    erp_document_number: str | None = None,
) -> str:
    """Имя .msg для OData: номер документа 1С (АЛ00-000762.msg).

    Толстый клиент 1С открывает письма как Outlook MSG, не RFC822/.eml.
    RFC822 конвертируется в MSG с нормализацией имён вложений (NFC, MIME).
    """
    number = (erp_document_number or "").strip()
    if number and number not in _SKIP_ERP_DOCUMENT_NUMBERS:
        return f"{number}.msg"
    return "Входящее_письмо.msg"


def erp_email_upload_marker_names(erp_document_number: str | None) -> set[str]:
    """Варианты имени одного письма (.msg по номеру 1С и legacy .eml/.msg)."""
    names = {
        ERP_LEGACY_EMAIL_FILENAME,
        ERP_LEGACY_EML_BASENAME,
        "Входящее_письмо.msg",
    }
    number = (erp_document_number or "").strip()
    if number and number not in _SKIP_ERP_DOCUMENT_NUMBERS:
        names.add(number)
        names.add(f"{number}.msg")
        names.add(f"{number}.eml")
    return names


def erp_email_skip_filenames(
    erp_document_number: str | None,
    skip_filenames: set[str] | None = None,
) -> set[str]:
    """Расширяет skip множеством имён письма для идемпотентной догрузки."""
    skip = {name.strip() for name in (skip_filenames or set()) if name and name.strip()}
    skip |= erp_email_upload_marker_names(erp_document_number)
    return skip


def erp_email_already_uploaded(
    erp_document_number: str | None,
    skip_filenames: set[str] | None = None,
) -> bool:
    skip = {name.strip() for name in (skip_filenames or set()) if name and name.strip()}
    if not skip:
        return False
    return bool(skip & erp_email_upload_marker_names(erp_document_number))


# Обратная совместимость имён функций (ранее .eml)
erp_eml_upload_marker_names = erp_email_upload_marker_names
erp_eml_skip_filenames = erp_email_skip_filenames
erp_eml_already_uploaded = erp_email_already_uploaded


def _build_synthetic_eml_bytes(email: EmailMessage) -> bytes:
    """Собирает .eml из полей EmailMessage, если IMAP недоступен."""
    from email import policy
    from email.message import EmailMessage as StdEmailMessage
    from email.utils import format_datetime, formataddr

    msg = StdEmailMessage(policy=policy.SMTP)
    header_id = email.message_id.split("#", 1)[0].strip()
    if header_id:
        if not header_id.startswith("<"):
            header_id = f"<{header_id}>"
        msg["Message-ID"] = header_id
    if email.sender_name:
        msg["From"] = formataddr((email.sender_name, email.sender_email))
    elif email.sender_email:
        msg["From"] = email.sender_email
    if email.to:
        msg["To"] = ", ".join(email.to)
    if email.cc:
        msg["Cc"] = ", ".join(email.cc)
    if email.reply_to:
        msg["Reply-To"] = email.reply_to
    msg["Subject"] = email.subject or ""
    msg["Date"] = format_datetime(email.received_at)
    msg["MIME-Version"] = "1.0"

    body_text = email.body_text or ""
    if email.body_html:
        msg.set_content(body_text, subtype="plain", charset="utf-8")
        msg.add_alternative(email.body_html, subtype="html", charset="utf-8")
    else:
        msg.set_content(body_text, subtype="plain", charset="utf-8")

    for att in attachments_with_content(email):
        mime = (att.mime_type or "application/octet-stream").split(";", 1)[0].strip()
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(
            bytes(att.content),
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=erp_attachment_filename(att),
        )
    return msg.as_bytes()


def _fetch_full_email_bytes_from_imap(email: EmailMessage, vault: VaultClient) -> bytes | None:
    if not email.mailbox:
        return None
    imap_id = email.message_id.split("#", 1)[0]
    settings = get_settings()
    try:
        credentials = resolve_imap_credentials(email.mailbox, vault)
        client = ImapMailboxClient(email.mailbox, credentials, settings=settings)
        raw = client.fetch_raw_rfc822_bytes(
            imap_id,
            timeout_sec=settings.imap_download_timeout_sec,
        )
    except Exception as exc:
        logger.warning(
            "erp_full_email_imap_fetch_failed",
            message_id=email.message_id,
            mailbox=email.mailbox,
            error=str(exc),
        )
        return None
    if not raw:
        logger.info(
            "erp_full_email_imap_not_found",
            message_id=email.message_id,
            mailbox=email.mailbox,
        )
        return None
    logger.info(
        "erp_full_email_imap_fetched",
        message_id=email.message_id,
        size_bytes=len(raw),
    )
    return raw


def ensure_full_email_bytes_for_erp(email: EmailMessage, vault: VaultClient | None) -> bytes:
    """RFC822 из IMAP (или синтетический .eml) для прикрепления к документу 1С."""
    if email.mailbox:
        key = full_email_cache_key(email.mailbox, email.message_id)
        cached = get_cached_attachment(key)
        if cached and cached.content:
            return cached.content

    raw: bytes | None = None
    if vault is not None and email.mailbox:
        raw = _fetch_full_email_bytes_from_imap(email, vault)
    if not raw:
        raw = _build_synthetic_eml_bytes(email)

    if email.mailbox and raw:
        put_cached_attachment(
            full_email_cache_key(email.mailbox, email.message_id),
            content=raw,
            mime_type="message/rfc822",
            filename=ERP_FULL_EMAIL_FILENAME,
        )
    return raw


def _collect_erp_upload_files(
    email: EmailMessage,
    *,
    full_email_bytes: bytes,
    erp_document_number: str | None = None,
    skip_filenames: set[str] | None = None,
    processed_at: datetime | None = None,
) -> list[AttachedFileInput]:
    """Полное письмо .msg + отдельные MIME-вложения (PDF и др.) для открытия в 1С."""
    skip = {name.strip() for name in (skip_filenames or set()) if name and name.strip()}
    attach_time = processed_at or now_attached_file_processed_at()
    files: list[AttachedFileInput] = []

    if not erp_email_already_uploaded(erp_document_number, skip):
        msg_name = erp_full_email_filename(email, erp_document_number=erp_document_number)
        if not full_email_bytes:
            return files
        if not msg_name.lower().endswith(".msg"):
            raise ValueError(f"ERP upload expects .msg filename, got {msg_name!r}")
        msg_bytes = eml_bytes_to_msg_bytes(full_email_bytes)
        files.append(
            AttachedFileInput(
                filename=msg_name,
                content=msg_bytes,
                processed_at=attach_time,
            )
        )

    for att in attachments_with_content(email):
        fname = erp_attachment_filename(att)
        if fname in skip:
            continue
        files.append(
            AttachedFileInput(
                filename=fname,
                content=bytes(att.content),
                processed_at=attach_time,
            )
        )
    return files


def _supports_attachment_upload(integration: IntegrationService) -> bool:
    if isinstance(integration, ODataIntegrationService):
        return bool(getattr(integration, "_attach_files_enabled", True))
    attach_fn = getattr(integration, "attach_files_to_incoming_correspondence", None)
    if not callable(attach_fn):
        return False
    base_fn = IntegrationService.attach_files_to_incoming_correspondence
    return getattr(attach_fn, "__func__", attach_fn) is not base_fn


def attach_email_files_to_document(
    integration: IntegrationService,
    *,
    document_ref_key: str,
    email: EmailMessage,
    vault: VaultClient | None = None,
    erp_document_number: str | None = None,
) -> list[dict]:
    """Прикрепляет полное письмо (.msg) и MIME-вложения к документу 1С."""
    if not _supports_attachment_upload(integration):
        logger.info(
            "erp_attach_files_skipped",
            reason="integration_does_not_support_attachments",
            message_id=email.message_id,
        )
        return []

    if vault is not None:
        ensure_attachment_bytes_for_erp(email, vault)
    full_email_bytes = ensure_full_email_bytes_for_erp(email, vault)

    files = _collect_erp_upload_files(
        email,
        full_email_bytes=full_email_bytes,
        erp_document_number=erp_document_number,
    )
    if not files:
        logger.info(
            "erp_attach_files_skipped",
            reason="no_files_to_attach",
            message_id=email.message_id,
            attachment_count=len(email.attachments or []),
        )
        return []

    try:
        results = integration.attach_files_to_incoming_correspondence(
            document_ref_key=document_ref_key,
            files=files,
        )
    except Exception:
        logger.exception(
            "erp_attach_files_failed",
            document_ref_key=document_ref_key,
            message_id=email.message_id,
            files=len(files),
            payload_fields=[erp_full_email_filename(email, erp_document_number=erp_document_number)],
        )
        raise

    logger.info(
        "erp_attach_files_done",
        document_ref_key=document_ref_key,
        message_id=email.message_id,
        attached=len(results),
        erp_document_number=erp_document_number,
    )
    return results


def attach_missing_email_files_to_document(
    integration: IntegrationService,
    *,
    document_ref_key: str,
    email: EmailMessage,
    vault: VaultClient | None = None,
    skip_filenames: set[str] | None = None,
    erp_document_number: str | None = None,
) -> list[dict]:
    """Прикрепляет недостающее полное письмо (.msg) и MIME-вложения к документу 1С."""
    if not _supports_attachment_upload(integration):
        logger.info(
            "erp_attach_files_skipped",
            reason="integration_does_not_support_attachments",
            message_id=email.message_id,
        )
        return []

    if vault is not None:
        ensure_attachment_bytes_for_erp(email, vault)
    full_email_bytes = ensure_full_email_bytes_for_erp(email, vault)

    files = _collect_erp_upload_files(
        email,
        full_email_bytes=full_email_bytes,
        erp_document_number=erp_document_number,
        skip_filenames=skip_filenames,
    )
    if not files:
        logger.info(
            "erp_attach_files_skipped",
            reason="no_new_attachments",
            message_id=email.message_id,
            skipped=len(skip_filenames or ()),
        )
        return []

    try:
        results = integration.attach_files_to_incoming_correspondence(
            document_ref_key=document_ref_key,
            files=files,
        )
    except Exception:
        logger.exception(
            "erp_attach_files_failed",
            document_ref_key=document_ref_key,
            message_id=email.message_id,
            files=len(files),
            payload_fields=[erp_full_email_filename(email, erp_document_number=erp_document_number)],
        )
        raise

    logger.info(
        "erp_attach_files_done",
        document_ref_key=document_ref_key,
        message_id=email.message_id,
        attached=len(results),
        skipped_existing=len(skip_filenames or ()),
        erp_document_number=erp_document_number,
    )
    return results
