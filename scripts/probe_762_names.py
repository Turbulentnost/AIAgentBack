"""Probe attachment filename encoding inside 762 MSG vs EML."""
from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import olefile  # noqa: E402
from email import policy  # noqa: E402
from email.parser import BytesParser  # noqa: E402

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.email_msg import (  # noqa: E402
    eml_bytes_to_msg_bytes,
    normalize_attachment_filename,
)
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402

EML = ROOT / "data/temp/compare_762/АЛ00-000762_2026-07-24T07-17-23_АЛ00-000762.eml"
REF_762 = "c18a2339-872c-11f1-984b-6cb31113810e"
REF_877 = "278fa9aa-8675-11f1-984a-6cb31113810e"
ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"


def names_from_msg(path: Path) -> list[dict]:
    ole = olefile.OleFileIO(str(path))
    out: list[dict] = []
    index = 0
    while ole.exists(("__attach_version1.0_#%08X" % index, "__substg1.0_3707001F")):
        for prop in ("3707001F", "3704001F", "370D001F", "370E001F"):
            stream = ("__attach_version1.0_#%08X" % index, f"__substg1.0_{prop}")
            if not ole.exists(stream):
                continue
            data = ole.openstream(stream).read()
            text = data.decode("utf-16-le", errors="replace").split("\x00")[0]
            out.append(
                {
                    "i": index,
                    "prop": prop,
                    "text": text,
                    "bytes": len(data),
                    "has_combining": any(unicodedata.combining(c) for c in text),
                    "is_nfc": unicodedata.normalize("NFC", text) == text,
                }
            )
        index += 1
    ole.close()
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    eml_bytes = EML.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(eml_bytes)
    eml_names = []
    for part in msg.walk():
        fn = part.get_filename()
        if fn:
            eml_names.append(
                {
                    "raw": fn,
                    "nfc": normalize_attachment_filename(fn),
                    "has_combining": any(unicodedata.combining(c) for c in fn),
                }
            )

    reconv = eml_bytes_to_msg_bytes(eml_bytes)
    out_dir = ROOT / "data/temp/compare_762"
    reconv_path = out_dir / "_names762.msg"
    reconv_path.write_bytes(reconv)

    settings = get_settings()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    fm = load_attached_file_field_map()
    odata_bytes = read_attached_file_storage_bytes(
        client, entity=ENTITY, ref_key=REF_762, field_map=fm
    )
    odata_path = out_dir / "_names762_odata.msg"
    odata_path.write_bytes(odata_bytes)

    odata_877 = read_attached_file_storage_bytes(
        client, entity=ENTITY, ref_key=REF_877, field_map=fm
    )
    path_877 = out_dir / "_names877.msg"
    path_877.write_bytes(odata_877)

    report = {
        "eml_attachment_names": eml_names,
        "reconv_msg_names": names_from_msg(reconv_path),
        "odata_762_names": names_from_msg(odata_path),
        "odata_877_names": names_from_msg(path_877),
        "reconv_eq_odata": reconv == odata_bytes,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
