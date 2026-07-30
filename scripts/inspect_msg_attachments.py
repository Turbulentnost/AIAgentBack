"""Compare Aspose MSG attachment internals: working 760 vs broken 762/884."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FILES = {
    "760-ok": ROOT / "data/temp/compare_762/АЛ00-000760_2026-07-23T14-22-45_АЛ00-000760.msg",
    "762-broken-msg": ROOT / "data/temp/compare_762/АЛ00-000762_2026-07-24T07-17-23_АЛ00-000762.msg",
    "762-eml": ROOT / "data/temp/compare_762/АЛ00-000762_2026-07-24T07-17-23_АЛ00-000762.eml",
    "877-ok": ROOT / "data/temp/compare_762/НП00-003877_2026-07-23T10-40-52_НП00-003877.msg",
    "884-broken": ROOT / "data/temp/compare_762/НП00-003884_2026-07-23T14-46-47_НП00-003884.msg",
}


def inspect_msg(path: Path) -> dict:
    if not path.exists():
        return {"missing": str(path)}
    try:
        from aspose.email_foss import msg as msgmod

        m = msgmod.MapiMessage.from_file(str(path))
        atts = []
        for i, a in enumerate(getattr(m, "attachments", None) or []):
            content = getattr(a, "content_stream", None) or b""
            atts.append(
                {
                    "i": i,
                    "display_name": getattr(a, "display_name", None),
                    "long_file_name": getattr(a, "long_file_name", None),
                    "mime_tag": getattr(a, "mime_tag", None),
                    "extension": getattr(a, "extension", None),
                    "content_size": len(content),
                    "pdf_magic": content[:5] == b"%PDF-" if content else False,
                    "type_name": type(a).__name__,
                }
            )
        return {
            "size": path.stat().st_size,
            "subject": getattr(m, "subject", None),
            "body_len": len(getattr(m, "body", "") or ""),
            "body_html_len": len(getattr(m, "body_html", "") or ""),
            "attachments": atts,
        }
    except Exception as exc:
        return {"error": str(exc)}


def inspect_eml(path: Path) -> dict:
    if not path.exists():
        return {"missing": str(path)}
    from email import policy
    from email.parser import BytesParser

    content = path.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(content)
    atts = []
    for part in msg.walk():
        fn = part.get_filename()
        disp = (part.get_content_disposition() or "").lower()
        if fn or disp == "attachment":
            payload = part.get_payload(decode=True) or b""
            atts.append(
                {
                    "name": fn,
                    "ctype": part.get_content_type(),
                    "size": len(payload),
                    "pdf": payload[:5] == b"%PDF-",
                }
            )
    return {"subject": msg.get("Subject"), "attachments": atts, "size": len(content)}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = {}
    for label, path in FILES.items():
        if label.endswith("-eml"):
            report[label] = inspect_eml(path)
        else:
            report[label] = inspect_msg(path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
