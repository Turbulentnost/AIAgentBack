"""Конвертация RFC822 (.eml) в Outlook MSG (OLE/MAPI).

Используется в тестах и диагностических скриптах. В ERP-потоке письмо
прикрепляется как исходный RFC822 (.eml): MSG от Aspose с PDF-вложениями
не открывается в толстом клиенте 1С.
"""

from __future__ import annotations

import os
import tempfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def is_msg_bytes(content: bytes) -> bool:
    """True, если байты похожи на Compound File Binary (.msg)."""
    return len(content) >= 8 and content[:8] == _OLE_MAGIC


def eml_bytes_to_msg_bytes(eml_bytes: bytes) -> bytes:
    """Конвертирует RFC822 в Outlook .msg (pure Python, Linux/Docker)."""
    if not eml_bytes:
        raise ValueError("empty eml_bytes")
    from aspose.email_foss import msg as msgmod

    email_message = BytesParser(policy=policy.default).parsebytes(eml_bytes)
    message = msgmod.MapiMessage.from_email_message(email_message)
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
