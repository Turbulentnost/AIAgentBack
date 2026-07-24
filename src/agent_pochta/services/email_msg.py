"""Конвертация RFC822 (.eml) в Outlook MSG (OLE/MAPI) для прикрепления к 1С."""

from __future__ import annotations

import os
import tempfile
import unicodedata
from email import policy
from email.parser import BytesParser
from pathlib import Path

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_MIME_BY_EXT = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain",
    "zip": "application/zip",
}


def is_msg_bytes(content: bytes) -> bool:
    """True, если байты похожи на Compound File Binary (.msg)."""
    return len(content) >= 8 and content[:8] == _OLE_MAGIC


def normalize_attachment_filename(name: str | None) -> str | None:
    """NFC + basename: 1С/Outlook ломаются на NFD-именах вроде «Райффайзен.pdf»."""
    if not name:
        return None
    cleaned = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not cleaned:
        return None
    return unicodedata.normalize("NFC", cleaned)


def _guess_mime_from_filename(filename: str) -> str | None:
    if "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[-1].lower()
    return _MIME_BY_EXT.get(ext)


def _normalize_eml_attachment_headers(email_message) -> None:
    """NFC-имена и MIME по расширению в RFC822 до Aspose (иначе NFD остаётся в OLE)."""
    for part in email_message.walk():
        raw_name = part.get_filename()
        if not raw_name:
            continue
        name = normalize_attachment_filename(raw_name)
        if not name:
            continue
        if part.get("Content-Type"):
            part.set_param("name", name, header="Content-Type")
        if part.get("Content-Disposition"):
            part.set_param("filename", name, header="Content-Disposition")
        guessed = _guess_mime_from_filename(name)
        if guessed and part.get_content_type() == "application/octet-stream":
            part.set_type(guessed)
            part.set_param("name", name, header="Content-Type")


def _fix_mapi_attachments(message) -> None:
    """Доп. нормализация MAPI после from_email_message (дублирует EML-правки)."""
    for att in message.attachments or []:
        raw_name = (
            getattr(att, "filename", None)
            or getattr(att, "long_file_name", None)
            or getattr(att, "display_name", None)
        )
        name = normalize_attachment_filename(raw_name)
        if not name:
            continue
        if hasattr(att, "filename"):
            att.filename = name
        if hasattr(att, "display_name"):
            att.display_name = name
        if hasattr(att, "long_file_name"):
            att.long_file_name = name
        if "." in name and hasattr(att, "extension"):
            att.extension = name.rsplit(".", 1)[-1].lower()
        mime = _guess_mime_from_filename(name)
        if mime:
            if hasattr(att, "mime_type"):
                att.mime_type = mime
            if hasattr(att, "mime_tag"):
                att.mime_tag = mime


def eml_body_only_bytes(eml_bytes: bytes) -> bytes:
    """RFC822 без частей-вложений (только заголовки и тело письма)."""
    from email.message import EmailMessage

    msg = BytesParser(policy=policy.default).parsebytes(eml_bytes)
    out = EmailMessage()
    for header in ("From", "To", "Cc", "Subject", "Date", "Message-ID", "Reply-To"):
        value = msg.get(header)
        if value:
            out[header] = value
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        out.set_content("")
        return out.as_bytes()
    content = body.get_content()
    subtype = body.get_content_subtype()
    charset = body.get_content_charset() or "utf-8"
    if subtype == "html":
        out.set_content(content, subtype="html", charset=charset)
    else:
        out.set_content(content, charset=charset)
    return out.as_bytes()


def eml_bytes_to_msg_bytes(eml_bytes: bytes, *, embed_attachments: bool = True) -> bytes:
    """Конвертирует RFC822 в Outlook .msg (pure Python, Linux/Docker).

    embed_attachments=False — только тело письма (1С/Outlook ломаются на некоторых PDF).
    """
    if not eml_bytes:
        raise ValueError("empty eml_bytes")
    from aspose.email_foss import msg as msgmod

    source = eml_bytes if embed_attachments else eml_body_only_bytes(eml_bytes)
    email_message = BytesParser(policy=policy.default).parsebytes(source)
    _normalize_eml_attachment_headers(email_message)
    message = msgmod.MapiMessage.from_email_message(email_message)
    _fix_mapi_attachments(message)
    with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as tmp:
        path = tmp.name
    try:
        message.save(path)
        data = Path(path).read_bytes()
    finally:
        os.unlink(path)
    if not is_msg_bytes(data):
        raise ValueError("MSG conversion did not produce OLE compound file")
    return data
