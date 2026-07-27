from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.tools.Outlook.outlook_config import OutlookConfig


def outlook_web_app_base(config: OutlookConfig) -> str | None:
    explicit = (config.web_app_url or "").strip()
    if explicit:
        return explicit.rstrip("/") + "/"
    server = config.server.strip()
    if not server:
        return None
    if server.startswith("http://") or server.startswith("https://"):
        return server.rstrip("/") + "/owa/"
    return f"https://{server.rstrip('/')}/owa/"


def build_outlook_meeting_url(config: OutlookConfig, item: Any) -> str | None:
    query = getattr(item, "web_client_read_form_query_string", None)
    if isinstance(query, str) and query.strip():
        normalized = query.strip()
        if normalized.startswith("http://") or normalized.startswith("https://"):
            return normalized
        base = outlook_web_app_base(config)
        if not base:
            return None
        if normalized.startswith("/"):
            host = base[: -len("/owa/")] if base.endswith("/owa/") else base.rstrip("/")
            if normalized.startswith("/owa/"):
                return f"{host}{normalized}"
            return f"{base.rstrip('/')}{normalized}"
        if normalized.startswith("?"):
            separator = "" if base.endswith("/") else "/"
            return f"{base}{separator}?{normalized.lstrip('?')}"
        return f"{base}?{normalized}"

    item_id = getattr(item, "id", None)
    base = outlook_web_app_base(config)
    if item_id and base:
        return f"{base}?ItemID={quote(str(item_id), safe='')}&exvsurl=1"
    return None


def calendar_item_outlook_meta(item: Any, config: OutlookConfig) -> dict[str, str | None]:
    refresh = getattr(item, "refresh", None)
    if callable(refresh):
        try:
            refresh()
        except Exception:
            pass
    return {
        "outlook_item_id": getattr(item, "id", None),
        "outlook_changekey": getattr(item, "changekey", None),
        "outlook_meeting_url": build_outlook_meeting_url(config, item),
    }
