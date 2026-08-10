from __future__ import annotations

import re
import uuid
from pathlib import Path

LOGO_CONTENT_ID = "platform-logo@avion-feedback"
OUTLOOK_PR_ATTACH_CONTENT_ID = "http://schemas.microsoft.com/mapi/proptag/0x3712001F"
OUTLOOK_PR_ATTACHMENT_HIDDEN = "http://schemas.microsoft.com/mapi/proptag/0x7FFE000B"


def make_attachment_content_id(filename: str) -> str:
    stem = Path(filename).stem
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip("-").lower()[:40] or "file"
    return f"{safe}-{uuid.uuid4().hex[:10]}@avion-feedback"


def resolve_platform_logo_bytes() -> bytes | None:
    app_dir = Path(__file__).resolve().parents[1]
    platform_root = Path(__file__).resolve().parents[3]
    for path in (
        app_dir / "assets" / "platform-logo.png",
        platform_root / "AIAgentFront" / "public" / "platform-logo.png",
        platform_root / "AIAgentFront" / "dist" / "platform-logo.png",
    ):
        if path.is_file():
            return path.read_bytes()
    return None


def set_outlook_attachment_content_id(outlook_attachment, content_id: str) -> None:
    outlook_attachment.PropertyAccessor.SetProperty(
        OUTLOOK_PR_ATTACH_CONTENT_ID,
        f"<{content_id}>",
    )


def hide_outlook_attachment(outlook_attachment) -> None:
    outlook_attachment.PropertyAccessor.SetProperty(OUTLOOK_PR_ATTACHMENT_HIDDEN, True)
