"""Quick test: EML NFC normalize before MSG conversion."""
from __future__ import annotations

import json
import sys
import tempfile
import unicodedata
from email import policy
from email.parser import BytesParser
from pathlib import Path

import olefile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.services.email_msg import (  # noqa: E402
    _guess_mime_from_filename,
    normalize_attachment_filename,
)

EML = ROOT / "data/temp/compare_762/АЛ00-000762_2026-07-24T07-17-23_АЛ00-000762.eml"


def ole_attach_info(path: Path) -> dict:
    ole = olefile.OleFileIO(str(path))
    name = ole.openstream(("__attach_version1.0_#00000000", "__substg1.0_3707001F")).read()
    mime = ole.openstream(("__attach_version1.0_#00000000", "__substg1.0_370E001F")).read()
    ole.close()
    name_txt = name.decode("utf-16-le").split("\x00")[0]
    mime_txt = mime.decode("utf-16-le").split("\x00")[0]
    return {
        "name": name_txt,
        "has_combining": any(unicodedata.combining(c) for c in name_txt),
        "mime": mime_txt,
    }


def main() -> None:
    from aspose.email_foss import msg as msgmod

    eml_bytes = EML.read_bytes()
    email = BytesParser(policy=policy.default).parsebytes(eml_bytes)
    for part in email.walk():
        fn = part.get_filename()
        if not fn:
            continue
        nfc = normalize_attachment_filename(fn)
        if part.get("Content-Type"):
            part.set_param("name", nfc, header="Content-Type")
        if part.get("Content-Disposition"):
            part.set_param("filename", nfc, header="Content-Disposition")
        guessed = _guess_mime_from_filename(nfc or "")
        if guessed and part.get_content_type() == "application/octet-stream":
            part.set_type(guessed)
            part.set_param("name", nfc, header="Content-Type")

    message = msgmod.MapiMessage.from_email_message(email)
    for att in message.attachments or []:
        nfc = normalize_attachment_filename(
            getattr(att, "long_file_name", None) or getattr(att, "display_name", None)
        )
        if not nfc:
            continue
        att.display_name = nfc
        att.long_file_name = nfc
        guessed = _guess_mime_from_filename(nfc)
        if guessed:
            att.mime_tag = guessed

    out = ROOT / "data/temp/compare_762/_testfix.msg"
    with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as tmp:
        path = tmp.name
    try:
        message.save(path)
        out.write_bytes(Path(path).read_bytes())
    finally:
        Path(path).unlink(missing_ok=True)

    print(json.dumps(ole_attach_info(out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
