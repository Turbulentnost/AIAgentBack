"""Analyze MSG embedded attachments for 760 vs 762."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402

ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
REFS = {
    "760-ok": "27997dc5-8689-11f1-984a-6cb31113810e",
    "762-new": "b664d818-8729-11f1-984b-6cb31113810e",
}


def analyze(content: bytes, path: Path) -> dict:
    path.write_bytes(content)
    try:
        from aspose.email_foss import msg as msgmod

        m = msgmod.MapiMessage.from_file(str(path))
        atts = []
        for i, a in enumerate(getattr(m, "attachments", None) or []):
            data = getattr(a, "content_stream", None) or b""
            name = getattr(a, "display_name", None) or getattr(a, "long_file_name", None)
            atts.append(
                {
                    "i": i,
                    "name": name,
                    "name_repr": repr(name),
                    "mime": getattr(a, "mime_tag", None),
                    "size": len(data),
                    "pdf": data[:5] == b"%PDF-" if data else False,
                    "ole": data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" if len(data) >= 8 else False,
                }
            )
        return {
            "subject": getattr(m, "subject", None),
            "body_len": len(getattr(m, "body", "") or ""),
            "attachments": atts,
        }
    except Exception as exc:
        return {"error": str(exc)}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    fm = load_attached_file_field_map()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    out_dir = ROOT / "data" / "temp" / "compare_762"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {}
    for label, ref in REFS.items():
        content = read_attached_file_storage_bytes(
            client, entity=ENTITY, ref_key=ref, field_map=fm
        )
        path = out_dir / f"{label}.msg"
        report[label] = {
            "ref": ref,
            "bytes": len(content),
            "cfb": content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
            "msg": analyze(content, path),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
