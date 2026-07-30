"""Probe volume ACL and OData file storage entities."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

from agent_pochta.config import get_settings
from agent_pochta.services.odata_attached_file import (
    _DEFAULT_VOLUME_KEY,
    fetch_volume_root_from_odata,
)
from agent_pochta.services.odata_client import ODataClient

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    s = get_settings()
    client = ODataClient(
        s.odata_base_url,
        username=s.odata_username,
        password=s.odata_password,
        timeout_sec=120,
    )
    root = fetch_volume_root_from_odata(
        client, volume_key=s.odata_file_volume_key or _DEFAULT_VOLUME_KEY
    )
    print("volume_root", root)
    p = Path(root)
    try:
        print("exists", p.exists())
    except OSError as exc:
        print("exists_error", exc)
    try:
        test = p / "_agent_write_test_pochta"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        print("write_ok", True)
    except OSError as exc:
        print("write_ok", False, exc)

    base = s.odata_base_url.rstrip("/") + "/"
    auth = (s.odata_username, s.odata_password)
    text = httpx.get(base + "$metadata", auth=auth, timeout=120).text
    names = re.findall(r'EntityType Name="([^"]+)"', text)
    for n in names:
        if any(x in n for x in ("Файл", "Хранил", "Том", "Binary")):
            print("ET", n)


if __name__ == "__main__":
    main()
